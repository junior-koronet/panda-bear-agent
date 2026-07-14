"""
Skills: Generate Email / Generate Slack Message / Group New Hires / Notify Manager
All content generation capabilities. These PREPARE content but never send it.
Sending is always a separate step that requires approval.
"""

import os
from datetime import datetime
from groq import Groq
from skills.base import Skill

ONBOARDING_GUIDE_URL = "https://docs.google.com/document/d/1xa56Rg1uNwtPuy-eYHvqdfOKPAEKZ_fji8y0cRrQyc8/edit?tab=t.0"

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None


class GenerateEmailSkill(Skill):
    """
    Generates a welcome email for a new employee.
    Adjusts tone and language based on country rules.
    Always returns a draft — never sends.
    """
    name = "generate_email"
    description = "Generates a personalized welcome email for a new employee. Returns a draft for approval."
    category = "content"

    def _run(self, first_name: str, hire_date: str, language: str = "es", onboarding_time: str = "8:00 AM") -> dict:
        try:
            hd = datetime.strptime(hire_date, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "error": f"Invalid hire_date: {hire_date}"}

        if language == "es":
            date_str = hd.strftime("%d/%m/%Y")
            content = (
                f"Hola {first_name}! Todo bien, ¿y tú?\n\n"
                f"Gracias por escribir. Confirmo que tu primer día es el {date_str}. ¡Ya falta poco!\n\n"
                f"En los próximos días estarás recibiendo un correo de Jeisson, nuestro encargado de IT, "
                f"con las instrucciones para configurar tu correo corporativo y una guía de configuración "
                f"de herramientas. Una vez tengas acceso a ese correo, desde allí te estaremos enviando "
                f"la citación formal para tu sesión de onboarding, que será aproximadamente a las "
                f"{onboarding_time}.\n\n"
                f"Haremos todo lo posible para que todo fluya sin inconvenientes. "
                f"Cualquier duda, aquí estamos.\n\n"
                f"Un abrazo,\nJuni"
            )
        else:
            date_str = hd.strftime("%B %d, %Y")
            content = (
                f"Hi {first_name}! Hope you're doing well!\n\n"
                f"Just wanted to confirm that your first day is {date_str}. "
                f"We're so excited to have you on board!\n\n"
                f"In the coming days, you'll be receiving an email from Jeisson, our IT coordinator, "
                f"with instructions to set up your corporate email and a tools configuration guide. "
                f"Once you have access to that email, we'll send you the formal invitation for your "
                f"onboarding session, which will take place at approximately {onboarding_time}.\n\n"
                f"We'll do our best to make everything run smoothly. "
                f"If you have any questions, don't hesitate to reach out!\n\n"
                f"Best,\nJuni"
            )

        return {
            "result": content,
            "messageType": "employee_email",
            "language": language,
            "decision": f"Generated {language} welcome email for {first_name}",
            "reasoning": (
                f"Selected {language} language based on employee location. "
                f"Template includes IT setup instructions and onboarding session confirmation at {onboarding_time}."
            ),
            "confidence": 0.9,
        }


class GenerateSlackManagerSkill(Skill):
    """
    Generates a Slack message for the employee's manager.
    Includes the onboarding guide link.
    """
    name = "generate_slack_manager"
    description = "Generates a Slack message for the manager with the onboarding guide. Returns a draft for approval."
    category = "content"

    def _run(self, manager_name: str, employee_name: str, language: str = "es") -> dict:
        first_name = manager_name.split(",")[0].strip() if manager_name else "there"

        if language == "es":
            content = (
                f"Hola {first_name}! Dado que falta poquito para que {employee_name} ingrese, "
                f"te comparto la siguiente Guía de On Boarding!!!\n\n"
                f"{ONBOARDING_GUIDE_URL}\n\n"
                f"Cualquier cosa que necesites, ¡estamos a disposición! 🐼"
            )
        else:
            content = (
                f"Hi {first_name}! Since {employee_name} is starting soon, "
                f"I would like you to have the On Boarding Guide with some tips!\n\n"
                f"{ONBOARDING_GUIDE_URL}\n\n"
                f"Let me know if there is anything I can help you with!! 🐼"
            )

        return {
            "result": content,
            "messageType": "manager_slack",
            "language": language,
            "decision": f"Generated {language} manager Slack message for {employee_name}'s manager ({manager_name})",
            "reasoning": (
                f"Manager notification is required before {employee_name}'s first day. "
                f"Sharing the onboarding guide to help the manager prepare."
            ),
            "confidence": 0.95,
        }


class GenerateSlackBienvenidaSkill(Skill):
    """
    Generates the #Koronet welcome announcement.
    """
    name = "generate_slack_bienvenida"
    description = "Generates the #Koronet channel welcome announcement for a new employee. Returns a draft for approval."
    category = "content"

    def _run(self, first_name: str, last_name: str, job_title: str = "New Team Member") -> dict:
        content = (
            f"Hi team! @here A huge welcome to @{first_name} {last_name}, "
            f"joining as {job_title}. We are so excited to have you with us! 🎉"
        )

        return {
            "result": content,
            "messageType": "bienvenidas_slack",
            "language": "en",
            "decision": f"Generated #Koronet welcome announcement for {first_name} {last_name}",
            "reasoning": (
                f"Channel announcement is part of the standard onboarding flow. "
                f"Announcing {first_name} {last_name} as {job_title}."
            ),
            "confidence": 1.0,
        }


class RefineContentSkill(Skill):
    """
    Uses Groq AI to refine a message based on HR feedback.
    Responds to instructions like "make it warmer" or "make it shorter".
    """
    name = "refine_content"
    description = "Uses AI to improve a message based on HR feedback. Always returns a draft — never sends."
    category = "content"

    def _run(self, original_content: str, feedback: str, employee_name: str = "") -> dict:
        if not groq_client:
            return {"success": False, "error": "Groq client not configured"}

        prompt = (
            f"Eres Panda Bear, el agente de People Operations de Koronet.\n\n"
            f"Mensaje original para {employee_name or 'el empleado'}:\n\n"
            f"{original_content}\n\n"
            f"Instrucción de HR: {feedback}\n\n"
            f"Genera una versión mejorada del mensaje. Mantén el tono cálido y profesional. "
            f"Solo devuelve el mensaje mejorado, sin explicaciones adicionales."
        )

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.6,
        )
        refined = response.choices[0].message.content

        return {
            "result": refined,
            "originalContent": original_content,
            "feedback": feedback,
            "decision": f"Refined message based on HR feedback: '{feedback}'",
            "reasoning": f"Applied HR instruction to improve the message while maintaining appropriate tone.",
            "confidence": 0.85,
        }
