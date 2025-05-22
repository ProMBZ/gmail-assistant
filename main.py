import os
import streamlit as st
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import pickle
from email.mime.text import MIMEText
import base64
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

st.set_page_config(page_title="AI Email Assistant", page_icon="📬")
st.title("📬 AI Email Assistant")
st.write("Summarize unread emails, draft smart replies, and send them instantly with Gemini AI.")

# Initialize session state keys
if 'creds' not in st.session_state:
    st.session_state.creds = None
if 'service' not in st.session_state:
    st.session_state.service = None
if 'auth_url' not in st.session_state:
    st.session_state.auth_url = None
if 'flow' not in st.session_state:
    st.session_state.flow = None

llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    api_key=os.getenv("GEMINI_API_KEY")
)

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

def authenticate_manual():
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
            flow.fetch_token(code=code)
            creds = flow.credentials
            save_creds(creds)
            st.session_state.creds = creds
            st.session_state.service = build_service(creds)
            st.success("✅ Gmail connected successfully!")
            st.session_state.auth_url = None
            st.session_state.flow = None
            return True
        except Exception as e:
            st.error(f"Failed to fetch token: {e}")
            return False
    return False

def get_unread_emails(service):
    result = service.users().messages().list(userId='me', labelIds=['INBOX'], q='is:unread', maxResults=5).execute()
    messages = result.get('messages', [])
    if not messages:
        return []

    emails = []
    for msg in reversed(messages):
        data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = data['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
        snippet = data.get('snippet', '')
        thread_id = data.get('threadId')

        # Mark as read
        service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['UNREAD']}).execute()

        emails.append({'id': msg['id'], 'thread_id': thread_id, 'subject': subject, 'sender': sender, 'snippet': snippet})

    return emails

@st.cache_data(show_spinner=False)
def summarize_email(snippet):
    prompt = f"Summarize this email in bullet points:\n\n{snippet}"
    response = llm.invoke(prompt)
    return response.content if hasattr(response, 'content') else str(response)

@st.cache_data(show_spinner=False)
def generate_reply(snippet, user_instruction):
    prompt = f"Write a clear, confident, professional reply to this email based on the user's instructions.\n\nEmail: {snippet}\n\nUser instruction for the reply: {user_instruction}"
    response = llm.invoke(prompt)
    return response.content if hasattr(response, 'content') else str(response)

def get_or_create_label(service, label_name="Replied"):
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    for label in labels:
        if label['name'].lower() == label_name.lower():
            return label['id']
    label_body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show"
    }
    new_label = service.users().labels().create(userId='me', body=label_body).execute()
    return new_label['id']

def send_email(service, to, subject, message_text, thread_id=None):
    message = MIMEText(message_text)
    message['to'] = to
    message['subject'] = "Re: " + subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {'raw': raw}
    if thread_id:
        body['threadId'] = thread_id
    sent_message = service.users().messages().send(userId='me', body=body).execute()

    replied_label_id = get_or_create_label(service, "Replied")
    service.users().messages().modify(userId='me', id=sent_message['id'], body={'addLabelIds': [replied_label_id]}).execute()
    return sent_message

# Load creds from disk on app start
if not st.session_state.creds:
    creds = load_creds()
    if creds and creds.valid:
        st.session_state.creds = creds
        st.session_state.service = build_service(creds)
    elif creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_creds(creds)
        st.session_state.creds = creds
        st.session_state.service = build_service(creds)

if not st.session_state.creds:
    if st.button("🔗 Connect Gmail"):
        authenticate_manual()
else:
    service = st.session_state.service
    try:
        unread_emails = get_unread_emails(service)
        if unread_emails:
            st.success(f"You have {len(unread_emails)} unread emails!")
        else:
            st.info("No unread emails found.")

        for i, email in enumerate(unread_emails):
            st.divider()
            st.subheader(f"📧 Email #{i+1}: {email['subject']}")
            st.write(f"**From:** {email['sender']}")
            st.write(f"**Snippet:** {email['snippet']}")

            if f"summary_{i}" not in st.session_state:
                st.session_state[f"summary_{i}"] = summarize_email(email['snippet'])
            st.success("Summary:")
            st.markdown(st.session_state[f"summary_{i}"])

            user_details = st.text_input(f"Your name/role/company for Email #{i+1}", key=f"details_{i}")
            user_instruction = st.text_area(f"Instructions for reply (Email #{i+1})", value="Write a polite and relevant reply to this email.", key=f"instruction_{i}")

            if f"reply_{i}" not in st.session_state:
                enhanced_prompt = f"{user_instruction}\n\nUser details: {user_details}"
                st.session_state[f"reply_{i}"] = generate_reply(email['snippet'], enhanced_prompt)

            updated_reply = st.text_area("Edit reply before sending:", value=st.session_state[f"reply_{i}"], height=200, key=f"replybox_{i}")

            col1, col2, col3 = st.columns([1,1,1])
            with col1:
                if st.button(f"✅ Send Reply Email #{i+1}", key=f"send_{i}"):
                    to_email = email['sender'].split('<')[-1].replace('>', '') if '<' in email['sender'] else email['sender']
                    send_email(service, to_email, email['subject'], updated_reply, thread_id=email['thread_id'])
                    st.success(f"Reply sent to {to_email}!")
            with col2:
                if st.button(f"⏭️ Skip Email #{i+1}", key=f"skip_{i}"):
                    st.info(f"Skipped Email #{i+1}")
            with col3:
                if st.button(f"🔄 Refresh Reply #{i+1}", key=f"refresh_{i}"):
                    st.session_state[f"summary_{i}"] = summarize_email(email['snippet'])
                    enhanced_prompt = f"{user_instruction}\n\nUser details: {user_details}"
                    st.session_state[f"reply_{i}"] = generate_reply(email['snippet'], enhanced_prompt)
                    st.success("Reply regenerated.")

    except Exception as e:
        st.error(f"Error fetching emails or sending replies: {e}")
