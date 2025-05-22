import os
import pickle
import base64
from email.mime.text import MIMEText

import streamlit as st
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

load_dotenv()

# --- Constants ---
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# --- Streamlit UI setup ---
st.set_page_config(page_title="📬 AI Email Assistant", page_icon="📬")
st.title("📬 AI Email Assistant")
st.write("Summarize unread emails, draft smart replies, and send them instantly with Gemini AI.")

# --- Initialize session state ---
if 'creds' not in st.session_state:
    st.session_state.creds = None
if 'service' not in st.session_state:
    st.session_state.service = None
if 'auth_url' not in st.session_state:
    st.session_state.auth_url = None
if 'flow' not in st.session_state:
    st.session_state.flow = None
if 'auth_started' not in st.session_state:
    st.session_state.auth_started = False

# --- Setup Gemini AI client ---
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    api_key=os.getenv("GEMINI_API_KEY")
)

# --- Helper functions ---

def save_creds(creds):
    with open('token.pickle', 'wb') as token_file:
        pickle.dump(creds, token_file)

def load_creds():
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token_file:
            creds = pickle.load(token_file)
            return creds
    return None

def build_service(creds):
    return build('gmail', 'v1', credentials=creds)

def creds_valid(creds):
    return creds and creds.valid

def authenticate_manual():
    if st.session_state.flow is None:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        auth_url, _ = flow.authorization_url(prompt='consent')
        st.session_state.auth_url = auth_url
        st.session_state.flow = flow

    st.info("1️⃣ Click the link below to authorize your Gmail access (opens in a new tab):")
    st.markdown(f'<a href="{st.session_state.auth_url}" target="_blank" rel="noopener noreferrer">Authorize Gmail Access</a>', unsafe_allow_html=True)
    st.write("2️⃣ After signing in, copy the authorization code you receive.")

    code = st.text_input("Paste the authorization code here:")
    if code:
        try:
            st.session_state.flow.fetch_token(code=code)
            creds = st.session_state.flow.credentials
            save_creds(creds)
            st.session_state.creds = creds
            st.session_state.service = build_service(creds)
            st.success("✅ Gmail connected successfully!")

            # Reset auth flow state
            st.session_state.auth_url = None
            st.session_state.flow = None
            st.session_state.auth_started = False

            st.experimental_rerun()
            return True
        except Exception as e:
            st.error(f"Failed to fetch token: {e}")
            return False
    return False

def get_unread_emails(service):
    results = service.users().messages().list(userId='me', labelIds=['UNREAD'], maxResults=10).execute()
    messages = results.get('messages', [])
    emails = []
    for message in messages:
        msg = service.users().messages().get(userId='me', id=message['id'], format='full').execute()
        headers = msg['payload']['headers']

        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "(No Subject)")
        sender = next((h['value'] for h in headers if h['name'] == 'From'), "(Unknown Sender)")
        snippet = msg.get('snippet', '')
        thread_id = msg.get('threadId')

        emails.append({
            'id': message['id'],
            'thread_id': thread_id,
            'subject': subject,
            'sender': sender,
            'snippet': snippet
        })
    return emails

def summarize_email(email_snippet):
    prompt = f"Summarize this email snippet in 2 sentences:\n\n{email_snippet}"
    try:
        response = llm.invoke(prompt)
        return response.text.strip()
    except Exception as e:
        return "Error summarizing email."

def generate_reply(email_snippet, user_instruction):
    prompt = f"{user_instruction}\n\nEmail snippet:\n{email_snippet}\n\nWrite a professional reply:"
    try:
        response = llm.invoke(prompt)
        return response.text.strip()
    except Exception as e:
        return "Error generating reply."

def get_or_create_label(service, label_name):
    # Check existing labels
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    for label in labels:
        if label['name'] == label_name:
            return label['id']
    # Create label if not found
    label_body = {
        'name': label_name,
        'labelListVisibility': 'labelShow',
        'messageListVisibility': 'show'
    }
    label = service.users().labels().create(userId='me', body=label_body).execute()
    return label['id']

def send_email(service, to, subject, message_text, thread_id=None):
    message = MIMEText(message_text)
    message['to'] = to
    message['subject'] = "Re: " + subject if not subject.lower().startswith("re:") else subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    body = {
        'raw': raw_message,
        'threadId': thread_id
    }

    sent_message = service.users().messages().send(userId='me', body=body).execute()

    # Mark thread as read and move to "Replied" label
    service.users().threads().modify(userId='me', id=thread_id, body={
        'removeLabelIds': ['UNREAD']
    }).execute()

    replied_label_id = get_or_create_label(service, "Replied")
    service.users().threads().modify(userId='me', id=thread_id, body={
        'addLabelIds': [replied_label_id]
    }).execute()

    return sent_message

# --- App logic starts here ---

# Load credentials from file
creds = load_creds()
if creds_valid(creds):
    st.session_state.creds = creds
    st.session_state.service = build_service(creds)
elif creds and creds.expired and creds.refresh_token:
    creds.refresh(Request())
    save_creds(creds)
    st.session_state.creds = creds
    st.session_state.service = build_service(creds)
else:
    st.session_state.creds = None
    st.session_state.service = None

# Show Connect Gmail button if not connected
if st.session_state.creds is None:
    if st.button("🔗 Connect Gmail"):
        st.session_state.auth_started = True
        st.experimental_rerun()

# If auth started, show manual auth flow
if st.session_state.auth_started:
    authenticate_manual()

# Show emails if connected
if st.session_state.creds:
    service = st.session_state.service
    try:
        unread_emails = get_unread_emails(service)
        if unread_emails:
            st.success(f"You have {len(unread_emails)} unread emails.")
        else:
            st.info("No unread emails found.")

        for i, email in enumerate(unread_emails):
            st.divider()
            st.subheader(f"📧 Email #{i+1}: {email['subject']}")
            st.write(f"**From:** {email['sender']}")
            st.write(f"**Snippet:** {email['snippet']}")

            # Summarize email snippet once and store
            summary_key = f"summary_{email['id']}"
            if summary_key not in st.session_state:
                st.session_state[summary_key] = summarize_email(email['snippet'])
            st.markdown(f"**Summary:** {st.session_state[summary_key]}")

            # Get user input for personalized details and instructions
            user_details = st.text_input(f"Your name/role/company for Email #{i+1}", key=f"details_{email['id']}")
            user_instruction = st.text_area(f"Instructions for reply (Email #{i+1})", value="Write a polite and relevant reply to this email.", key=f"instruction_{email['id']}")

            # Generate reply once and store
            reply_key = f"reply_{email['id']}"
            if reply_key not in st.session_state:
                prompt_text = f"{user_instruction}\n\nUser details: {user_details}"
                st.session_state[reply_key] = generate_reply(email['snippet'], prompt_text)

            updated_reply = st.text_area("Edit reply before sending:", value=st.session_state[reply_key], height=200, key=f"replybox_{email['id']}")

            col1, col2, col3 = st.columns([1,1,1])
            with col1:
                if st.button(f"✅ Send Reply Email #{i+1}", key=f"send_{email['id']}"):
                    to_email = email['sender'].split('<')[-1].replace('>', '') if '<' in email['sender'] else email['sender']
                    send_email(service, to_email, email['subject'], updated_reply, thread_id=email['thread_id'])
                    st.success(f"Reply sent to {to_email}!")
                    # Clear cached reply and summary for this email after sending
                    del st.session_state[summary_key]
                    del st.session_state[reply_key]
            with col2:
                if st.button(f"⏭️ Skip Email #{i+1}", key=f"skip_{email['id']}"):
                    st.info(f"Skipped Email #{i+1}")
            with col3:
                if st.button(f"🔄 Refresh Reply #{i+1}", key=f"refresh_{email['id']}"):
                    st.session_state[summary_key] = summarize_email(email['snippet'])
                    prompt_text = f"{user_instruction}\n\nUser details: {user_details}"
                    st.session_state[reply_key] = generate_reply(email['snippet'], prompt_text)
                    st.success("Reply regenerated.")

    except Exception as e:
        st.error(f"Error fetching emails or sending replies: {e}")
