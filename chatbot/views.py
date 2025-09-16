from django.shortcuts import render, redirect
from django.http import JsonResponse
import markdown2
from django.contrib import auth
from django.contrib.auth.models import User
from .models import Chat, UploadedPDF
from django.utils import timezone
from openai import OpenAI
import os


# ✅ LangChain imports for RAG
from langchain.text_splitter import CharacterTextSplitter
from PyPDF2 import PdfReader
import re
from difflib import SequenceMatcher

from django.conf import settings
from django.db import IntegrityError, OperationalError, DatabaseError

# ✅ OpenRouter client setup (lazy)
def get_openrouter_client():
    api_key = getattr(settings, 'OPENROUTER_API_KEY', None)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=15.0,
    )

# ---------------------------
#  Simple Text Similarity Search
# ---------------------------
def find_relevant_chunks(query, chunks, top_k=3):
    """Find most relevant chunks using simple text similarity"""
    query_lower = query.lower()
    query_words = set(query_lower.split())
    
    scored_chunks = []
    
    for i, chunk in enumerate(chunks):
        chunk_lower = chunk.lower()
        chunk_words = set(chunk_lower.split())
        
        # Calculate word overlap score
        word_overlap = len(query_words.intersection(chunk_words))
        word_ratio = word_overlap / len(query_words) if query_words else 0
        
        # Calculate sequence similarity
        sequence_similarity = SequenceMatcher(None, query_lower, chunk_lower).ratio()
        
        # Combined score
        combined_score = (word_ratio * 0.7) + (sequence_similarity * 0.3)
        
        scored_chunks.append((combined_score, i, chunk))
    
    # Sort by score and return top chunks
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    return [chunk for score, idx, chunk in scored_chunks[:top_k]]

from django.contrib import messages

# ---------------------------
#  PDF Upload View (original)
# ---------------------------
def upload_pdf(request):
    if request.method == 'POST' and request.FILES.get('pdf'):
        try:
            pdf_file = request.FILES['pdf']
            
            # Validate file type
            if not pdf_file.name.lower().endswith('.pdf'):
                messages.error(request, "❌ Please upload a valid PDF file.")
                return redirect('upload_pdf')

            pdf_obj = UploadedPDF.objects.create(user=request.user, file=pdf_file)
            process_pdf(pdf_obj)  # extract + embed

            messages.success(request, f"✅ {pdf_file.name} uploaded successfully!")
            return redirect('chatbot')  # go back to chatbot after upload

        except Exception as e:
            if 'pdf_obj' in locals():
                pdf_obj.delete()
            messages.error(request, f"❌ Error processing PDF: {str(e)}")
            return redirect('upload_pdf')

    return render(request, 'upload_pdf.html')

# ---------------------------
#  Utility: Extract + Process PDF
# ---------------------------
def process_pdf(pdf_obj):
    """Extract text, split into chunks, and save for simple text search"""
    try:
        pdf_path = pdf_obj.file.path
        reader = PdfReader(pdf_path)

        # Extract text
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""

        # Check if text was extracted
        if not text.strip():
            raise ValueError("No text could be extracted from the PDF")

        # Split into chunks
        splitter = CharacterTextSplitter(
            separator="\n",
            chunk_size=1000,  # Larger chunks for better context
            chunk_overlap=200,
            length_function=len
        )
        chunks = splitter.split_text(text)

        # Ensure chunks is a list and not empty
        if not chunks or not isinstance(chunks, list):
            raise ValueError("Failed to split text into chunks")
        
        # Filter out empty chunks
        chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
        
        if not chunks:
            raise ValueError("No valid text chunks found after splitting")

        print(f"Successfully extracted {len(chunks)} chunks from PDF: {pdf_obj.file.name}")
        print(f"First chunk preview: {chunks[0][:100]}...")

        # Save chunks as text file (simple approach)
        chunks_text = "\n\n---CHUNK_SEPARATOR---\n\n".join(chunks)
        chunks_path = os.path.join(settings.MEDIA_ROOT, "pdfs", f"chunks_{pdf_obj.id}.txt")
        # Ensure directory exists (important on Render)
        os.makedirs(os.path.dirname(chunks_path), exist_ok=True)
        with open(chunks_path, 'w', encoding='utf-8') as f:
            f.write(chunks_text)

        # Save path to DB
        pdf_obj.faiss_index_path = chunks_path  # Reusing this field for chunks path
        pdf_obj.save()
        
        print(f"Successfully processed PDF: {pdf_obj.file.name}")
        print(f"Chunks saved to: {chunks_path}")
        
    except Exception as e:
        print(f"Error processing PDF {pdf_obj.file.name}: {str(e)}")
        raise e



# ---------------------------
#  LLM Chat (with optional RAG)
# ---------------------------
def ask_openai(message, user=None):
    """
    If user has uploaded PDFs, do RAG retrieval before sending to LLM.
    Otherwise, send plain query.
    """
    try:
        context = ""
        if user:
            pdfs = UploadedPDF.objects.filter(user=user)
            if pdfs.exists():
                # Load latest PDF's chunks
                last_pdf = pdfs.last()
                if last_pdf.faiss_index_path and os.path.exists(last_pdf.faiss_index_path):
                    try:
                        # Read chunks from file
                        with open(last_pdf.faiss_index_path, 'r', encoding='utf-8') as f:
                            chunks_text = f.read()
                        
                        # Split chunks
                        chunks = [chunk.strip() for chunk in chunks_text.split("---CHUNK_SEPARATOR---") if chunk.strip()]
                        
                        if chunks:
                            # Find relevant chunks using simple text similarity
                            relevant_chunks = find_relevant_chunks(message, chunks, top_k=3)
                            context = "\n\n".join(relevant_chunks)
                            print(f"Found {len(relevant_chunks)} relevant chunks for query: {message[:50]}...")
                        
                    except Exception as e:
                        print(f"Error reading PDF chunks: {str(e)}")

        # Build final prompt
        if context:
            prompt = f"""Based on the following document content, please answer the user's question. If the answer is not in the document, say so clearly.

Document Content:
{context}

User Question: {message}

Please provide a helpful and accurate answer based on the document content."""
        else:
            prompt = message

        # Call DeepSeek model
        try:
            client = get_openrouter_client()
            completion = client.chat.completions.create(
                model="deepseek/deepseek-chat-v3.1:free",
                messages=[{"role": "user", "content": prompt}],
                timeout=90  # 90 second timeout for the API call
            )
            answer = completion.choices[0].message.content.strip()
            return answer
        except Exception as llm_err:
            error_msg = str(llm_err)
            if "timeout" in error_msg.lower():
                return "The AI model is taking too long to respond. Please try again with a shorter question."
            elif "api" in error_msg.lower() or "key" in error_msg.lower():
                return "Error contacting AI model. Please check the API configuration."
            else:
                return f"Error contacting model: {error_msg}"
    except Exception as e:
        return f"Error: {str(e)}"


# ---------------------------
#  Chatbot View
# ---------------------------
def chatbot_view(request):
    chats = []
    if request.user.is_authenticated:
        chats = Chat.objects.filter(user=request.user).order_by("created_at")
        for chat in chats:
            chat.response = markdown2.markdown(
                chat.response,
                extras=["fenced-code-blocks", "tables", "strike", "cuddled-lists"]
            )

    if request.method == 'POST':
        try:
            message = request.POST.get('message')
            if not message:
                return JsonResponse({'error': 'No message provided'}, status=400)
            
            response = ask_openai(message, request.user if request.user.is_authenticated else None)

            formatted_response = markdown2.markdown(
                response,
                extras=["fenced-code-blocks", "tables", "strike", "cuddled-lists"]
            )

            if request.user.is_authenticated:
                Chat.objects.create(
                    user=request.user,
                    message=message,
                    response=response,
                    created_at=timezone.now()
                )

            return JsonResponse({'message': message, 'response': formatted_response})
        except Exception as e:
            return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)

    return render(request, 'chatbot.html', {'chats': chats})


# ---------------------------
#  Test View for Debugging
# ---------------------------
def test_pdf_processing(request):
    """Test view to debug PDF processing"""
    if not request.user.is_authenticated:
        return redirect('login')
    
    try:
        pdfs = UploadedPDF.objects.filter(user=request.user)
        if pdfs.exists():
            last_pdf = pdfs.last()
            if last_pdf.faiss_index_path and os.path.exists(last_pdf.faiss_index_path):
                with open(last_pdf.faiss_index_path, 'r', encoding='utf-8') as f:
                    chunks_text = f.read()
                chunks = [chunk.strip() for chunk in chunks_text.split("---CHUNK_SEPARATOR---") if chunk.strip()]
                return JsonResponse({
                    'status': 'success', 
                    'message': f'PDF processing working. Found {len(chunks)} chunks.',
                    'chunks_count': len(chunks),
                    'first_chunk_preview': chunks[0][:100] if chunks else 'No chunks'
                })
            else:
                return JsonResponse({'status': 'error', 'message': 'No processed PDF found'})
        else:
            return JsonResponse({'status': 'error', 'message': 'No PDFs uploaded'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Error: {str(e)}'})

# ---------------------------
#  PDF Upload View
# ---------------------------
def upload_pdf(request):
    if request.method == 'POST' and request.FILES.get('pdf'):
        try:
            pdf_file = request.FILES['pdf']
            
            # Validate file type
            if not pdf_file.name.lower().endswith('.pdf'):
                return render(request, 'upload_pdf.html', {'error_message': 'Please upload a PDF file'})
            
            pdf_obj = UploadedPDF.objects.create(user=request.user, file=pdf_file)
            process_pdf(pdf_obj)  # extract + embed
            return redirect('chatbot')
            
        except Exception as e:
            # If processing fails, delete the PDF object
            if 'pdf_obj' in locals():
                pdf_obj.delete()
            return render(request, 'upload_pdf.html', {'error_message': f'Error processing PDF: {str(e)}'})
    
    return render(request, 'upload_pdf.html')


# ---------------------------
#  Authentication
# ---------------------------
def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        try:
            user = auth.authenticate(request, username=username, password=password)
            if user is not None:
                auth.login(request, user)
                return redirect('chatbot')
            else:
                return render(request, 'login.html', {'error_message': 'Invalid Username or Password'})
        except OperationalError as e:
            return render(request, 'login.html', {'error_message': f'Database error during login: {e}'})
        except DatabaseError as e:
            return render(request, 'login.html', {'error_message': f'Database error: {e}'})
    return render(request, 'login.html')


def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')

        if not username or not password1:
            return render(request, 'register.html', {'error_message': 'Username and password are required'})

        if password1 != password2:
            return render(request, 'register.html', {'error_message': "Password doesn't match"})

        try:
            user = User.objects.create_user(username=username, email=email, password=password1)
            auth.login(request, user)
            return redirect('chatbot')
        except IntegrityError:
            return render(request, 'register.html', {'error_message': 'Username already exists'})
        except OperationalError as e:
            return render(request, 'register.html', {'error_message': f'Database error during registration: {e}'})
        except Exception as e:
            return render(request, 'register.html', {'error_message': f'Error creating account: {e}'})
    return render(request, 'register.html')


def logout_view(request):
    auth.logout(request)
    return redirect('chatbot')
