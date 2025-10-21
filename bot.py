import os
import logging
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from io import BytesIO
import database as db
from asyncio import Task

# Import your custom entities builder
from TGentities import build_text_with_entities

from telegram import Update, Bot
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, ApplicationHandlerStop

# Load environment variables from .env file
load_dotenv()

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
TELEGRAM_MESSAGE_LIMIT = 4096
COMMAND_TIMEOUT = 3600

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- AUTHORIZATION CHECKS ---
def is_owner(user_id: int) -> bool: return user_id == OWNER_ID
def is_authorized(user_id: int) -> bool: return is_owner(user_id) or db.is_sudo(user_id)

# --- HELPER FUNCTIONS ---
async def log_to_owner(bot: Bot, message_template: str):
    try:
        clean_text, entities = build_text_with_entities(message_template)
        await bot.send_message(chat_id=OWNER_ID, text=clean_text, entities=entities)
    except Exception as e:
        logger.error(f"Failed to send log message to owner: {e}")

async def ignore_edited_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Ignoring edited command")
    raise ApplicationHandlerStop

# --- BACKGROUND TASK LOGIC ---
async def run_shell_process(command_to_run: str, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    command_with_redirect = f"{command_to_run} 2>&1"
    process = None
    output = ""
    log_status_text = "Executed with errors"
    return_code = -1

    try:
        process = await asyncio.create_subprocess_shell(
            command_with_redirect,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        context.user_data['running_process'] = process
        
        stdout_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)
        
        output = stdout_bytes.decode('utf-8', errors='replace').strip()
        log_status_text = "Executed successfully" if process.returncode == 0 else "Executed with errors"

    except asyncio.TimeoutError:
        output = f"Error: Command timed out after {COMMAND_TIMEOUT} seconds."
    except asyncio.CancelledError:
        log_status_text = "Cancelled by user"
        raise
    except Exception as e:
        output = f"An unexpected error occurred: {e}"
    finally:
        if process and process.returncode is None:
            process.kill()
        if 'running_process' in context.user_data:
            del context.user_data['running_process']
        return_code = process.returncode if process else -1

    return output, return_code, log_status_text

# --- MAIN COMMAND HANDLERS ---
async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        logger.warning(f"Unauthorized shell attempt by {update.effective_user.name} [{update.effective_user.id}]")
        return

    if context.user_data.get('running_task'):
        await update.message.reply_text("A shell command is already running. Use /stoptasks to cancel.")
        return

    command_to_run = " ".join(context.args)
    if not command_to_run:
        await update.message.reply_text("Usage: /shell <command>")
        return

    executing_template = "<code>Executing...</code>"
    clean_text, entities = build_text_with_entities(executing_template)
    feedback_message = await update.message.reply_text(text=clean_text, entities=entities)

    output = ""
    return_code = -1
    log_status_text = "Unknown"
    
    try:
        task = asyncio.create_task(run_shell_process(command_to_run, context))
        context.user_data['running_task'] = task
        output, return_code, log_status_text = await task
    except asyncio.CancelledError:
        output = "Task was cancelled by user."
        log_status_text = "Cancelled by user"
    finally:
        if 'running_task' in context.user_data:
            del context.user_data['running_task']
        await feedback_message.delete()

    user = update.effective_user
    chat = update.effective_chat
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    # --- FIX: Enhanced chat info with a clickable message link ---
    chat_info_line = ""
    if chat.type != 'private':
        if chat.username:
            # Public group/channel with a username
            message_link = f"https://t.me/{chat.username}/{update.effective_message.id}"
            chat_info_line = f"<b>Initiated in:</b> <a href=\"{message_link}\">{chat.title}</a> [<code>{chat.id}</code>]\n"
        else:
            # Private supergroup (ID starts with -100)
            stripped_chat_id = str(chat.id).replace("-100", "")
            message_link = f"https://t.me/c/{stripped_chat_id}/{update.effective_message.id}"
            chat_info_line = f"<b>Initiated in:</b> <a href=\"{message_link}\">{chat.title}</a> [<code>{chat.id}</code>]\n"
    else:
        chat_info_line = "<b>Initiated in:</b> PM\n"

    return_code_info = f"(Return Code: <code>{return_code}</code>)"
    log_template_to_owner = (
        f"🖥️ <b>Shell Command Executed</b>\n\n"
        f"<b>User:</b> <a href=\"tg://user?id={user.id}\">{user.full_name}</a> [<code>{user.id}</code>]\n"
        f"{chat_info_line}"
        f"<b>Command:</b> <code>{command_to_run}</code>\n"
        f"<b>Time:</b> <code>{timestamp}</code>\n\n"
        f"<b>Status: {log_status_text}.</b> {return_code_info}"
    )
    await log_to_owner(context.bot, log_template_to_owner)

    if not output.strip():
        output = "Command executed with no output."
        
    final_output = f"~$ {command_to_run}\n\n{output}"

    if len(final_output) > TELEGRAM_MESSAGE_LIMIT:
        output_file = BytesIO(final_output.encode('utf-8'))
        output_file.name = f"Shell_output_{update.effective_message.id}.txt"
        caption_template = f"<pre>Output too long, sent as file.</pre>"
        clean_caption, entities = build_text_with_entities(caption_template)
        try:
            await context.bot.send_document(
                chat_id=chat.id, document=output_file, caption=clean_caption, caption_entities=entities,
                reply_to_message_id=update.effective_message.id
            )
        except BadRequest as e:
            if "replied message not found" in str(e).lower():
                await context.bot.send_document(
                    chat_id=chat.id, document=output_file, caption=clean_caption, caption_entities=entities
                )
            else: raise e
    else:
        reply_template = f"<pre>{final_output}</pre>"
        clean_text, entities = build_text_with_entities(reply_template)
        try:
            await context.bot.send_message(
                chat_id=chat.id, text=clean_text, entities=entities,
                reply_to_message_id=update.effective_message.id
            )
        except BadRequest as e:
            if "replied message not found" in str(e).lower():
                await context.bot.send_message(
                    chat_id=chat.id, text=clean_text, entities=entities
                )
            else: raise e

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_authorized(user_id):
        await update.message.reply_text("Welcome, authorized user. Use /shell or /sh to execute commands.")
    else:
        logger.warning(f"Unauthorized /start attempt by {update.effective_user.name} [{user_id}]")

async def stoptasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        logger.warning(f"Unauthorized /stoptasks attempt by {user.name} [{user.id}]")
        return

    task: Task = context.user_data.get('running_task')
    if not task or task.done():
        await update.message.reply_text("There are no active tasks to stop.")
        return

    try:
        task.cancel()
        logger.info(f"User {user.name} [{user.id}] cancelled a running task.")
        await update.message.reply_text(f"<b>Stopping active task...</b>", ParseMode='HTML')
    except Exception as e:
        logger.error(f"Error while trying to cancel a task: {e}")
        await update.message.reply_text(f"An error occurred: {e}")

async def addsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE): 
    if not is_owner(update.effective_user.id):
        logger.warning(f"Unauthorized /addsudo attempt by {update.effective_user.name} [{update.effective_user.id}]")
        return
    try:
        target_id = int(context.args[0])
        target_name = f"User {target_id}"
        try:
            target_user = await context.bot.get_chat(chat_id=target_id)
            target_name = f"<a href=\"tg://user?id={target_id}\">{target_user.first_name}</a>"
        except BadRequest:
            logger.warning(f"Could not fetch info for user ID {target_id}.")
            target_name = f"User [<code>{target_id}</code>]"

        if db.add_sudo(target_id):
            reply_template = f"<b>Success:</b> {target_name} has been added to sudoers."
            logger.info(f"Owner {update.effective_user.id} added {target_id} to sudo list.")
        else:
            reply_template = f"<b>Info:</b> {target_name} is already a sudoer."
        
        clean_text, entities = build_text_with_entities(reply_template)
        await update.message.reply_text(text=clean_text, entities=entities)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /addsudo <user_id>")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def delsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        logger.warning(f"Unauthorized /delsudo attempt by {update.effective_user.name} [{update.effective_user.id}]")
        return
    try:
        target_id = int(context.args[0])
        target_name = f"User {target_id}"
        try:
            target_user = await context.bot.get_chat(chat_id=target_id)
            target_name = f"<a href=\"tg://user?id={target_id}\">{target_user.first_name}</a>"
        except BadRequest:
            logger.warning(f"Could not fetch info for user ID {target_id}.")
            target_name = f"User [<code>{target_id}</code>]"
        
        if db.del_sudo(target_id):
            reply_template = f"<b>Success:</b> {target_name} has been removed from sudoers."
            logger.info(f"Owner {update.effective_user.id} removed {target_id} from sudo list.")
        else:
            reply_template = f"<b>Info:</b> {target_name} was not found in sudoers."
        
        clean_text, entities = build_text_with_entities(reply_template)
        await update.message.reply_text(text=clean_text, entities=entities)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /delsudo <user_id>")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def sudos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        logger.warning(f"Unauthorized /sudos attempt by {update.effective_user.name} [{update.effective_user.id}]")
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

def main():
    if not TELEGRAM_BOT_TOKEN or not OWNER_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN and OWNER_ID must be set in the .env file.")
    db.init_db()
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
    
    application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.COMMAND, ignore_edited_commands), group=-1)
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler(["shell", "sh"], shell_command))
    application.add_handler(CommandHandler("addsudo", addsudo_command))
    application.add_handler(CommandHandler("delsudo", delsudo_command))
    application.add_handler(CommandHandler("sudos", sudos_command))
    application.add_handler(CommandHandler("stoptasks", stoptasks_command))
    
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
