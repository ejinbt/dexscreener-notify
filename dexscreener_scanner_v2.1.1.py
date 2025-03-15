# dexscreener scanner v2.1.1
# author - rapidfreelancin
import sys
import undetected_chromedriver as uc
from seleniumbase import SB
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import re
import pandas as pd
import time
from telegram import Bot,Update
from telegram.ext import Application,ContextTypes, CommandHandler, CallbackContext
import asyncio
from sqlalchemy import create_engine , Column , String , Integer
from sqlalchemy.orm import sessionmaker , declarative_base
import requests
import json
import nest_asyncio
nest_asyncio.apply()


from pyvirtualdisplay import Display
display = Display(visible=0,size=(800,800),use_xauth=True)
display.start()

# Database Setup
DATABASE_URL = "sqlite:///ranks.db"
Base = declarative_base()
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

class TokenRank(Base):
    __tablename__ = "token_ranks"
    token = Column(String,primary_key=True)
    rank = Column(Integer,nullable=False)

Base.metadata.create_all(engine)


TELEGRAM_BOT_TOKEN = "7318957699:AAGgHEqKoz2hxYrd6d7__Km9RAhfhPailmQ"
TELEGRAM_CHAT_ID = "-4636119367"
rank_threshold = 30
bot = Bot(token=TELEGRAM_BOT_TOKEN)



async def send_telegram_message(message):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message,parse_mode="html")
        print(f"[DEBUG] Message sent: {message}")
    except Exception as e:
        print(f"[ERROR] Failed to send message: {e}")


def get_previous_rank(token):
    record = session.query(TokenRank).filter_by(token=token).first()
    return record.rank if record else None

async def help_command(update: Update, context: CallbackContext):
    help_text = (
        "<b>📌 DexScreener Rank Tracker Bot</b>\n\n"
        "🔹 <b>Features:</b>\n"
        "   • Tracks token rank movements every X seconds.\n"
        "   • Sends alerts for significant rank changes.\n"
        "   • Displays token details like age, buy/sell volume, and Dex address.\n"
        "   • Allows you to customize the rank change threshold.\n\n"
        "🔹 <b>Commands:</b>\n"
        "   • <b>/setrankchange X</b> - Set rank change threshold to X (default: 10).\n"
        "   • <b>/setinterval X</b> - Set interval  to X , should run this command once for bot to start.\n\"" \
        "   • <b>/scan X(pause,resume)</b> - Resumes or Pause the bot\n"
        "   • <b>/help</b> - Show this help message.\n\n"
        "🔗 <i>Made for monitoring trending tokens in real-time!</i>"
    )
    
    await update.message.reply_text(help_text, parse_mode="HTML", disable_web_page_preview=True)


def update_rank(token, rank):
    record = session.query(TokenRank).filter_by(token=token).first()
    if record:
        record.rank = rank
    else:
        session.add(TokenRank(token=token, rank=rank))
    session.commit()




async def set_rank_threshold(update: Update, context: CallbackContext):
    global rank_threshold
    try:
        new_threshold = int(context.args[0])
        rank_threshold = new_threshold
        await update.message.reply_text(f"Rank change threshold set to {new_threshold}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setrankchange <number>")


# Scrape dexscreener for data
async def scrape_dex(context:ContextTypes.DEFAULT_TYPE):
    global previous_ranks
    url = "https://dexscreener.com/?rankBy=trendingScoreH6&order=desc"
    agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0"
    # Initialize driver
    if "linux" in sys.platform:
        agent = None
    with SB(uc=True,xvfb=True,headless=False) as sb:

        try:
            sb.uc_open_with_reconnect(url,4)
            print(sb.get_page_title())
            sb.uc_gui_click_captcha()
            print(sb.get_page_title())
            # Wait for the table to load
            data_element = WebDriverWait(sb, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'ds-dex-table'))
            )

            if data_element:
                # Extract text from table, skipping first 12 lines (headers)
                data = data_element.text.split("\n")[12:]
                # Headers for DataFrame
                headers = ['RANK', 'TOKEN', 'PRICE', 'AGE']
                processed_data = []
                i = 0

                while i < len(data):
                    if data[i].startswith("#"):  # New row starts
                        rank = data[i].replace("#","")
                        i += 1  

                        # Extract Token Name (Find "/" first, then take full name)
                        token_name_parts = []
                        found_slash = False

                        while i < len(data) and not (data[i].startswith("$") and data[i][1:].replace(',', '').replace('.', '').isdigit()):
                            if data[i] == "/":  
                                # Merge the token before and after "/"
                                token_name_parts[-1] += "/" + data[i + 1]  
                                i += 1  # Skip the next element as it's already merged
                            else:
                                token_name_parts.append(data[i])
                            
                            i += 1

                        token_name = " ".join(token_name_parts)

                        # Ensure we have a valid price
                        if i < len(data) and data[i].startswith("$"):
                            price = data[i]
                            i += 1
                        else:
                            price = "N/A"  # Handle missing price

                        # Ensure valid age
                        if i < len(data):
                            age = data[i]
                            i += 1
                        else:
                            age = "N/A"

                        # Add processed row
                        processed_data.append([rank, token_name, price, age])
                    else:
                        i += 1  # Move forward if no match

                # Convert to DataFrame
                df = pd.DataFrame(processed_data, columns=headers) 

                # Detect drastic change in rank

                for _,row in df.iterrows():
                    token, current_rank, price, age = row['TOKEN'], int(row['RANK']), row['PRICE'], row['AGE']
                    previous_ranks = get_previous_rank(token)
                    if previous_ranks is not None:
                        rank_change = previous_ranks - current_rank
                        if rank_change >= rank_threshold:
                            if rank_change > 0:
                                temp_token_name = token.split()
                                for part in temp_token_name:
                                    if '/' in part:
                                        extracted_pair = part
                                        if age_to_minutes(age) < 360:
                                            dex_search = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={extracted_pair}")
                                            json_obj = dex_search.json()
                                            dex_address = json_obj["pairs"][0]["url"]
                                            vol_24h = json_obj["pairs"][0]["volume"]["h24"]
                                            txns_24h_buy = json_obj["pairs"][0]["txns"]["h24"]["buys"]
                                            txns_24h_sell = json_obj["pairs"][0]["txns"]["h24"]["sells"]
                                            total_txns_24h = int(txns_24h_buy) + int(txns_24h_sell)
                                            buy_vol_24h = (int(txns_24h_buy) / total_txns_24h) * vol_24h
                                            sell_vol_24h = (int(txns_24h_sell) / total_txns_24h) * vol_24h
                                            message = (
                                                f"📊 <b>Token Name:</b> {token}\n"
                                                f"📈 <b>Change in Rank:</b> {rank_change}\n"
                                                f"🏆 <b>Current Rank:</b> #{current_rank}\n"
                                                f"🕰 <b>Age:</b> {age}\n"
                                                f"🔗 <a href='{dex_address}'>View on DexScreener</a>\n\n"
                                                f"📉 <b>Buy/Sell (24H):</b> 🟢 {buy_vol_24h:.2f} / 🔴 {sell_vol_24h:.2f}"
                                            )
                                            #record = session.query(TokenRank).filter_by(token=token).first()
                                            #record.rank  = 0
                                            await send_telegram_message(message=message)
                    
                    update_rank(token,current_rank)
            else:
                print("Data element not found!")

        except Exception as e:
            print("Error:", e)  # Close driver after execution
 
def age_to_minutes(age):
    match = re.match(r"(\d+)([dhym])", age)
    if not match:
        return float("inf")  # Invalid format → treat as very large
    
    value, unit = int(match[1]), match[2]

    if unit == "m":  # Minutes
        return value
    elif unit == "h":  # Hours to minutes
        return value * 60
    elif unit == "d":  # Days to minutes
        return value * 24 * 60
    elif unit == "y":  # Years to minutes
        return value * 365 * 24 * 60


application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
job_queue = application.job_queue
interval = 60
async def setinterval(update,context:CallbackContext):
    global interval
    interval = int(context.args[0])
    remove_job(name="scrapedex",context=context)
    await update.message.reply_text(f"Starting to scan dexscreener with interval of {interval}")
    repeating_job = job_queue.run_repeating(scrape_dex,interval=interval,name="scrapedex",job_kwargs={"max_instances":1,'misfire_grace_time':10,})

def remove_job(name:str,context: ContextTypes.DEFAULT_TYPE) -> bool:
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True

async def handlePauseResume(update,context:CallbackContext):
    choice = context.args[0]
    if choice == "resume":
        resume(name="scrapedex",context=context)
        await update.message.reply_text(f"dexscreener scanner resumed scanning with interval of {interval}")
    elif choice == "pause":
        pause(name="scrapedex",context=context)
        await update.message.reply_text(f"dexscreener scanner paused scanning")

def pause(name:str,context: ContextTypes.DEFAULT_TYPE):
    current_jobs = context.job_queue.get_jobs_by_name(name)
    for job in current_jobs:
        job.enabled = False
def resume(name:str,context:ContextTypes.DEFAULT_TYPE):
    current_jobs = context.job_queue.get_jobs_by_name(name)
    for job in current_jobs:
        job.enabled = True


# Run the function
if __name__ == "__main__":
    application.add_handler(CommandHandler("setrankchange",set_rank_threshold))
    application.add_handler(CommandHandler("setinterval",setinterval))
    application.add_handler(CommandHandler("scan",handlePauseResume))
    application.add_handler(CommandHandler("help",help_command))
    application.run_polling()


