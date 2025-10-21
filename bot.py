import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from io import BytesIO
import database as db

# Import your custom entities builder
from TGentities import build_text_with_entities

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# Load environment variables from .env file
load_dotenv()

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
TELEGRAM_MESSAGE_LIMIT = 4096
COMMAND_TIMEOUT = 120 # Seconds

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- AUTHORIZATION CHECKS ---
def is_owner(user_id: int) -> bool:
    """Check if the user is the owner."""
    return user_id == OWNER_ID

def is_authorized(user_id: int) -> bool:
    """Check if the user is the owner or a sudo user."""
    return is_owner(user_id) or db.is_sudo(user_id)

# --- HELPER FUNCTIONS ---
async def log_to_owner(bot: Bot, message_template: str):
    """Sends a formatted log message to the bot owner using the custom entities builder."""
    try:
        clean_text, entities = build_text_with_entities(message_template)
        await bot.send_message(chat_id=OWNER_ID, text=clean_text, entities=entities)
    except Exception as e:
        logger.error(f"Failed to send log message to owner: {e}")

# --- COMMAND HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    user_id = update.effective_user.id
    if is_authorized(user_id):
        await update.message.reply_text("Welcome, authorized user. Use /shell or /sh to execute commands.")
    else:
        logger.warning(f"Unauthorized /start attempt by {update.effective_user.name} [{user_id}]")

async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /shell and /sh commands."""
    user = update.effective_user
    chat = update.effective_chat
    command_to_run = " ".join(context.args)
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    if not is_authorized(user.id):
        logger.warning(f"Unauthorized command attempt: User: {user.name} [{user.id}], Command: '{command_to_run}'")
        log_template = (
            f"⚠️ <b>Unauthorized Access Detected!</b>\n\n"
            f"<b>User:</b> {user.full_name} [<code>{user.id}</code>]\n"
            f"<b>Command:</b> <code>{command_to_run}</code>\n"
            f"<b>Time:</b> <code>{timestamp}</code>\n\n"
            f"<b>Action: The command has been blocked.</b>\n"
            f"<i>Note: This user is not authorized to use shell access.</i>"
        )
        await log_to_owner(context.bot, log_template)
        return

    if not command_to_run:
        await update.message.reply_text("Usage: /shell <command>")
        return

    executing_template = "<code>Executing...</code>"
    clean_text, entities = build_text_with_entities(executing_template)
    feedback_message = await update.message.reply_text(text=clean_text, entities=entities)

    logger.info(f"Executing command: '{command_to_run}' for user {user.name} [{user.id}]")
    
    try:
        process = await asyncio.create_subprocess_shell(
            command_to_run,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)

        stdout = stdout_bytes.decode('utf-8', errors='replace')
        stderr = stderr_bytes.decode('utf-8', errors='replace')
        
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += f"\n--- STDERR ---\n{stderr}"
        if not output.strip():
            output = "Command executed with no output."

    except asyncio.TimeoutError:
        process.kill()
        output = f"Error: Command timed out after {COMMAND_TIMEOUT} seconds."
    except Exception as e:
        output = f"An error occurred while executing the command: {e}"

    await feedback_message.delete()

    log_status_text = "Executed successfully" if not stderr else "Executed with errors"
    log_template_to_owner = (
        f"🖥️ <b>Shell Command Executed</b>\n\n"
        f"<b>User:</b> {user.full_name} [<code>{user.id}</code>]\n"
    )
    if chat.type != 'private':
         log_template_to_owner += f"<b>Chat:</b> {chat.title} [<code>{chat.id}</code>]\n"

    log_template_to_owner += (
        f"<b>Command:</b> <code>{command_to_run}</code>\n"
        f"<b>Time:</b> <code>{timestamp}</code>\n\n"
        f"<b>Status: {log_status_text}.</b>"
    )

    if not is_owner(user.id) or len(output) > 1000:
        log_file_content = f"Command: {command_to_run}\n\n--- OUTPUT ---\n{output}"
        output_file = BytesIO(log_file_content.encode('utf-8'))
        output_file.name = f"Shell_{update.effective_message.message_id}.txt"
        clean_caption, caption_entities = build_text_with_entities(log_template_to_owner)
        await context.bot.send_document(
            chat_id=OWNER_ID, document=output_file, caption=clean_caption, caption_entities=caption_entities
        )
    else:
        await log_to_owner(context.bot, log_template_to_owner)

    final_output = f"~$ {command_to_run}\n\n{output}"

    if len(final_output) > TELEGRAM_MESSAGE_LIMIT:
        output_file = BytesIO(final_output.encode('utf-8'))
        output_file.name = f"Shell_output_{update.effective_message.message_id}.txt"
        await update.message.reply_document(document=output_file, caption=f"Shell:\n<code>~$ {command_to_run}</code>")
    else:
        reply_template = f"Shell:\n<pre>~$ {command_to_run}\n\n{output}</pre>"
        clean_text, entities = build_text_with_entities(reply_template)
        await update.message.reply_text(text=clean_text, entities=entities)


async def addsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        logger.warning(f"Unauthorized /addsudo attempt by {user.name} [{user.id}]")
        return

    try:
        target_id = int(context.args[0])
        if db.add_sudo(target_id):
            reply_template = f"Success: User [<code>{target_id}</code>] has been added to sudoers."
            logger.info(f"Owner {user.id} added {target_id} to sudo list.")
        else:
            reply_template = f"Info: User [<code>{target_id}</code>] is already a sudoer."
        
        clean_text, entities = build_text_with_entities(reply_template)
        await update.message.reply_text(text=clean_text, entities=entities)

    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /addsudo <user_id>")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def delsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        logger.warning(f"Unauthorized /delsudo attempt by {user.name} [{user.id}]")
        return

    try:
        target_id = int(context.args[0])
        if db.del_sudo(target_id):
            reply_template = f"Success: User [<code>{target_id}</code>] has been removed from sudoers."
            logger.info(f"Owner {user.id} removed {target_id} from sudo list.")
        else:
            reply_template = f"Info: User [<code>{target_id}</code>] was not found in sudoers."

        clean_text, entities = build_text_with_entities(reply_template)
        await update.message.reply_text(text=clean_text, entities=entities)

    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /delsudo <user_id>")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def sudos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id):
        logger.warning(f"Unauthorized /sudos attempt by {user.name} [{user.id}]")
        return
    
    sudo_ids = db.get_all_sudos()
    
    if not sudo_ids:
        reply_template = "There are no sudo users."
    else:
        reply_template = "<b>Sudo Users:</b>\n\n"
        for user_id in sudo_ids:
            reply_template += f"• <code>{user_id}</code>\n"
            
    clean_text, entities = build_text_with_entities(reply_template)
    await update.message.reply_text(text=clean_text, entities=entities)


# --- FIX: Added the missing colon here ---
def main():
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN or not OWNER_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN and OWNER_ID must be set in the .env file.")

    db.init_db()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler(["shell", "sh"], shell_command))
    application.add_handler(CommandHandler("addsudo", addsudo_command))
    application.add_handler(CommandHandler("delsudo", delsudo_command))
    application.add_handler(CommandHandler("sudos", sudos_command))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
