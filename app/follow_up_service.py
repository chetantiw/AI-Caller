"""

app/follow_up_service.py

Follow-up automation service for WhatsApp (via teleCMI/PIOPIY) and email after calls.

Supports sending personalized follow-up messages based on call outcomes.

WhatsApp: Uses teleCMI/PIOPIY API instead of Twilio
Email: Uses yagmail for SMTP

"""


import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional

import httpx
import yagmail
from loguru import logger
from dotenv import load_dotenv

from app import database as db


load_dotenv()
os.makedirs("logs", exist_ok=True)
logger.add("logs/follow_up_service.log", rotation="100 MB", level="INFO", retention="7 days")


class FollowUpService:
    """Service for sending automated follow-ups after calls."""

    def __init__(self):
        self.httpx_client = httpx.AsyncClient(timeout=30.0)
        self.email_client = None
        self._init_clients()

    def _init_clients(self):
        """Initialize WhatsApp and email clients."""
        # teleCMI/PIOPIY for WhatsApp
        self.telecmi_app_id = os.getenv("PIOPIY_AGENT_ID")
        self.telecmi_app_secret = os.getenv("PIOPIY_AGENT_TOKEN")
        self.telecmi_whatsapp_number = os.getenv("PIOPIY_WHATSAPP_NUMBER")

        if self.telecmi_app_id and self.telecmi_app_secret and self.telecmi_whatsapp_number:
            logger.info("✅ teleCMI WhatsApp client initialized")
        else:
            logger.warning("⚠️  teleCMI WhatsApp credentials not found - WhatsApp follow-ups disabled")

        # Email client
        email_user = os.getenv("EMAIL_USER")
        email_password = os.getenv("EMAIL_PASSWORD")
        email_smtp = os.getenv("EMAIL_SMTP", "smtp.gmail.com")

        if email_user and email_password:
            try:
                self.email_client = yagmail.SMTP(email_user, email_password, host=email_smtp)
                logger.info("✅ Email client initialized")
            except Exception as e:
                logger.error(f"❌ Email client initialization failed: {e}")
        else:
            logger.warning("⚠️  Email credentials not found - Email follow-ups disabled")

    async def schedule_follow_up(self, call_id: int):
        """Schedule a follow-up for a completed call."""
        try:
            # Get call details
            call = db.get_call(call_id)
            if not call:
                logger.error(f"Call {call_id} not found for follow-up")
                return

            # Get campaign follow-up settings
            campaign = db.get_campaign(call['campaign_id'])
            if not campaign or not campaign.get('follow_up_enabled'):
                return  # No follow-up configured

            # Calculate follow-up time
            delay_minutes = campaign.get('follow_up_delay_minutes', 30)
            follow_up_time = datetime.now() + timedelta(minutes=delay_minutes)

            # Schedule the follow-up
            asyncio.create_task(self._send_follow_up_at_time(
                call, campaign, follow_up_time
            ))

            logger.info(f"📅 Follow-up scheduled for call {call_id} at {follow_up_time}")

        except Exception as e:
            logger.error(f"Error scheduling follow-up for call {call_id}: {e}")

    async def _send_follow_up_at_time(self, call: dict, campaign: dict, follow_up_time: datetime):
        """Wait until follow-up time and send the message."""
        # Calculate wait time
        wait_seconds = (follow_up_time - datetime.now()).total_seconds()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        try:
            await self._send_follow_up_message(call, campaign)
        except Exception as e:
            logger.error(f"Error sending follow-up for call {call['id']}: {e}")

    async def _send_follow_up_message(self, call: dict, campaign: dict):
        """Send the actual follow-up message."""
        follow_up_type = campaign.get('follow_up_type', 'whatsapp')
        message = self._generate_message(call, campaign)

        if follow_up_type in ['whatsapp', 'both']:
            await self._send_whatsapp_message(call, message)

        if follow_up_type in ['email', 'both']:
            await self._send_email_message(call, message)

        logger.info(f"✅ Follow-up sent for call {call['id']} via {follow_up_type}")

    def _generate_message(self, call: dict, campaign: dict) -> str:
        """Generate personalized follow-up message."""
        template = campaign.get('follow_up_message_template', '')

        if not template:
            # Default template
            template = """Hi {lead_name},

Thank you for speaking with us about {company}. I wanted to follow up on our conversation.

Based on our discussion, I believe our solution could help {company} achieve their goals.

Would you be available for a quick demo next week?

Best regards,
{agent_name}
MuTech Automation"""

        # Replace placeholders
        message = template.replace('{lead_name}', call.get('lead_name', 'there'))
        message = message.replace('{company}', call.get('company', 'your company'))
        message = message.replace('{agent_name}', 'AI Sales Agent')  # Could be made configurable

        # Add call outcome context if available
        if call.get('outcome') == 'interested':
            message += "\n\nI noticed you were interested in learning more - I'd love to schedule that demo!"
        elif call.get('outcome') == 'callback':
            message += "\n\nAs requested, following up on our earlier conversation."

        return message

    async def _send_whatsapp_message(self, call: dict, message: str):
        """Send WhatsApp message using teleCMI/PIOPIY API.

        Note: This implementation uses a generic API structure.
        Please verify and adjust the endpoint, headers, and payload
        according to teleCMI's official WhatsApp API documentation.
        """
        if not self.telecmi_app_id or not self.telecmi_app_secret or not self.telecmi_whatsapp_number:
            logger.warning("teleCMI WhatsApp credentials not available - skipping WhatsApp message")
            return

        try:
            # Format phone number for WhatsApp (must include country code)
            phone = call.get('phone', '')
            if not phone.startswith('+'):
                phone = f"+91{phone}"  # Default to India, could be made configurable

            # teleCMI WhatsApp API call
            # Adjust this according to teleCMI's actual API documentation
            api_url = f"https://api.telecmi.com/v1/whatsapp/message"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.telecmi_app_secret}"
            }
            payload = {
                "app_id": self.telecmi_app_id,
                "from": self.telecmi_whatsapp_number,
                "to": phone,
                "message": message,
                "type": "text"
            }

            response = await self.httpx_client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()

            result = response.json()
            logger.info(f"📱 WhatsApp message sent to {phone} via teleCMI - ID: {result.get('message_id', 'unknown')}")

        except Exception as e:
            logger.error(f"Error sending WhatsApp message via teleCMI: {e}")

    async def _send_email_message(self, call: dict, message: str):
        """Send email message."""
        if not self.email_client:
            logger.warning("Email client not available - skipping email message")
            return

        try:
            # Get lead email - this would need to be added to leads table
            # For now, we'll skip if no email available
            lead = db.get_lead(call.get('lead_id'))
            if not lead or not lead.get('email'):
                logger.warning(f"No email available for lead {call.get('lead_id')}")
                return

            subject = f"Follow-up from our conversation - {call.get('company', 'your company')}"

            self.email_client.send(
                to=lead['email'],
                subject=subject,
                contents=message
            )

            logger.info(f"📧 Email sent to {lead['email']}")

        except Exception as e:
            logger.error(f"Error sending email message: {e}")


# Global service instance
follow_up_service = FollowUpService()


async def schedule_call_follow_up(call_id: int):
    """Convenience function to schedule follow-up for a call."""
    await follow_up_service.schedule_follow_up(call_id)
