import os
import base64
import json
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from email.mime.text import MIMEText
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
from streamlit_autorefresh import st_autorefresh

# Load .env locally, but on Streamlit cloud secrets are better
load_dotenv()

st.set_page_config(page_title="AI Email Assistant", page_icon="📬")
st.title("📬 AI Email Assistant")
st.write("Summarize unread emails, draft smart replies, and send them instantly with Gemini AI.")

st_autorefresh(interval=300000, key="email_checker")

# Gemini LLM setup
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash-exp",
    api_key=st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
)

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

@st.cache_resource(show_spinner=False)
def authenticate_gmail():
    try:
        # Load service account JSON key from secrets
        service_account_info = json.loads(st.secrets["google"]["service_account_key"])
        creds = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES
        )
        # Impersonate user if needed:
        # creds = creds.with_subject("user-email@example.com")

        service = build("gmail", "v1", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Failed to authenticate Gmail: {e}")
        return None

def get_unread_emails(service):
    result = service.users().messages().list(userId='me', labelIds=['INBOX'], q='is:unread', maxResults=5).execute()
    messages = result.get('messages', [])[::-1]
    emails = []

    for msg in messages:
        data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = data['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
        snippet = data.get('snippet', '')
        thread_id = data.get('threadId')

        # Mark email as read
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
    body = {'raw': raw, 'threadId': thread_id} if thread_id else {'raw': raw}
    sent_message = service.users().messages().send(userId='me', body=body).execute()
    replied_label_id = get_or_create_label(service, "Replied")
    service.users().messages().modify(userId='me', id=sent_message['id'], body={'addLabelIds': [replied_label_id]}).execute()
    return sent_message

gmail_service = authenticate_gmail()

if gmail_service:
    if 'last_checked_count' not in st.session_state:
        st.session_state['last_checked_count'] = 0

    try:
        unread_emails = get_unread_emails(gmail_service)
        current_count = len(unread_emails)
        st.session_state['emails'] = unread_emails
        st.session_state['emails_loaded'] = True
        st.session_state['last_checked_count'] = current_count
    except Exception as e:
        st.error(f"Error during email fetch: {e}")

    emails = st.session_state.get('emails', [])
    if not emails:
        st.success("No unread emails found.")
    else:
        for i, email in enumerate(emails):
            st.divider()
            st.subheader(f"📧 Email #{i+1}: {email['subject']}")
            st.write(f"**From:** {email['sender']}")
            st.write(f"**Snippet:** {email['snippet']}")

            if f"summary_{i}" not in st.session_state:
                st.session_state[f"summary_{i}"] = summarize_email(email['snippet'])

            st.success("Summary:")
            st.markdown(st.session_state[f"summary_{i}"])

            user_details = st.text_input(f"Your Name, Role, or Company (Email #{i+1})", key=f"details_{i}")
            user_instruction = st.text_area(
                f"Instruction for Gemini (Email #{i+1})",
                value="Write a polite and relevant reply to this email.",
                key=f"instruction_{i}"
            )

            if f"reply_{i}" not in st.session_state:
                enhanced_prompt = f"{user_instruction}\n\nHere is the user's detail for context: {user_details}"
                st.session_state[f"reply_{i}"] = generate_reply(email['snippet'], enhanced_prompt)

            updated_reply = st.text_area(
                "Edit the reply before sending:",
                value=st.session_state[f"reply_{i}"],
                height=200,
                key=f"replybox_{i}"
            )

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button(f"✅ Send Reply #{i+1}", key=f"send_{i}"):
                    to_email = email['sender'].split('<')[-1].replace('>', '') if '<' in email['sender'] else email['sender']
                    send_email(gmail_service, to_email, email['subject'], updated_reply, thread_id=email['thread_id'])
                    st.success(f"Custom reply sent to {to_email} ✨")
                    st.session_state[f"sent_{i}"] = True

            with col2:
                if st.button(f"⏭️ Skip Email #{i+1}", key=f"skip_{i}"):
                    st.session_state[f"skipped_{i}"] = True
                    st.info(f"Skipped Email #{i+1} ✉️")

            with col3:
                if st.button(f"🔄 Refresh Email #{i+1}", key=f"refresh_{i}"):
                    st.session_state[f"summary_{i}"] = summarize_email(email['snippet'])
                    enhanced_prompt = f"{user_instruction}\n\nHere is the user's detail for context: {user_details}"
                    st.session_state[f"reply_{i}"] = generate_reply(email['snippet'], enhanced_prompt)
                    st.success("Reply regenerated.")
else:
    st.stop()
