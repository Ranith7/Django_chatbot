from django.shortcuts import render, redirect
from django.http import JsonResponse
import markdown2
from django.contrib import auth
from django.contrib.auth.models import User
from .models import Chat, UploadedPDF, ChatSession, Message
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
from django.middleware.csrf import get_token

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
#  LLM Chat (with conversation history and optional RAG)
# ---------------------------
def ask_openai(message, user=None, session=None):
    """
    If user has uploaded PDFs, do RAG retrieval before sending to LLM.
    Uses conversation history if session is provided.
    """
    try:
        # Build conversation history
        messages = []
        
        # Add system message (optional)
        messages.append({"role": "system", "content": "You are a helpful AI assistant."})
        
        # Add conversation history if session is provided
        if session:
            session_messages = Message.objects.filter(session=session).order_by('timestamp')
            for msg in session_messages:
                messages.append({"role": msg.role, "content": msg.content})
        
        # Add current user message
        messages.append({"role": "user", "content": message})
        
        # Handle RAG context if user has uploaded PDFs
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

        # If we have RAG context, modify the last user message to include it
        if context:
            messages[-1]["content"] = f"""Based on the following document content, please answer the user's question. If the answer is not in the document, say so clearly.

Document Content:
{context}

User Question: {message}

Please provide a helpful and accurate answer based on the document content."""

        # Call DeepSeek model with conversation history
        try:
            client = get_openrouter_client()
            completion = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=messages,
                timeout=90,
            )
            answer = completion.choices[0].message.content.strip()
            return answer
        except Exception as llm_err:
            # Log and return concise error to avoid 500s
            return f"Error contacting model: {str(llm_err)}"
    except Exception as e:
        return f"Error: {str(e)}"


# ---------------------------
#  Session Management Helper Functions
# ---------------------------
def get_or_create_session(user):
    """Get the user's active session or create a new one"""
    if not user.is_authenticated:
        return None
    
    # Try to get the most recent active session
    session = ChatSession.objects.filter(user=user, is_active=True).first()
    
    if not session:
        # Create a new session
        session = ChatSession.objects.create(user=user)
    
    return session

def get_session_messages(session):
    """Get all messages for a session, formatted for display"""
    if not session:
        return []
    
    messages = Message.objects.filter(session=session).order_by('timestamp')
    formatted_messages = []
    
    for msg in messages:
        formatted_messages.append({
            'role': msg.role,
            'content': msg.content,
            'timestamp': msg.timestamp,
            'formatted_content': markdown2.markdown(
                msg.content,
                extras=["fenced-code-blocks", "tables", "strike", "cuddled-lists"]
            ) if msg.role == 'assistant' else msg.content
        })
    
    return formatted_messages

# ---------------------------
#  Chatbot View
# ---------------------------
def chatbot_view(request):
    # Get or create session for authenticated users
    session = get_or_create_session(request.user)
    
    # Get session messages for display
    session_messages = get_session_messages(session) if session else []
    
    # Keep old chats for backward compatibility (optional)
    old_chats = []
    if request.user.is_authenticated:
        old_chats = Chat.objects.filter(user=request.user).order_by("created_at")
        for chat in old_chats:
            chat.response = markdown2.markdown(
                chat.response,
                extras=["fenced-code-blocks", "tables", "strike", "cuddled-lists"]
            )

    if request.method == 'POST':
        message = request.POST.get('message')
        
        # Get AI response with conversation history
        response = ask_openai(message, request.user if request.user.is_authenticated else None, session)

        formatted_response = markdown2.markdown(
            response,
            extras=["fenced-code-blocks", "tables", "strike", "cuddled-lists"]
        )

        # Save messages to session if user is authenticated
        if request.user.is_authenticated and session:
            # Save user message
            Message.objects.create(
                session=session,
                role='user',
                content=message
            )
            
            # Save assistant response
            Message.objects.create(
                session=session,
                role='assistant',
                content=response
            )
            
            # Update session timestamp
            session.updated_at = timezone.now()
            session.save()
            
            # Also save to old Chat model for backward compatibility
            Chat.objects.create(
                user=request.user,
                message=message,
                response=response,
                created_at=timezone.now()
            )

        return JsonResponse({
            'message': message, 
            'response': formatted_response,
            'session_id': str(session.session_id) if session else None
        })

    return render(request, 'chatbot.html', {
        'chats': old_chats,  # Keep for backward compatibility
        'session_messages': session_messages,
        'session_id': str(session.session_id) if session else None
    })


# ---------------------------
#  New Session View
# ---------------------------
def start_new_session(request):
    """Start a new conversation session"""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'User not authenticated'}, status=401)
    
    # Deactivate current active session
    ChatSession.objects.filter(user=request.user, is_active=True).update(is_active=False)
    
    # Create new session
    session = ChatSession.objects.create(user=request.user)
    
    return JsonResponse({
        'session_id': str(session.session_id),
        'message': 'New conversation started'
    })

# ---------------------------
#  Debug CSRF View
# ---------------------------
def debug_csrf(request):
    token = get_token(request)
    return JsonResponse({'csrf_token': token})


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
