#!/usr/bin/env python
# coding: utf-8

# # Gradio Code
# 
# This notebook runs an interactive NBA betting assistant designed to provide personalized picks, strategies, and guidance based on your experience level and goals. To get started, simply enter a username — this creates or resumes your personal betting session. You’ll be asked a few quick questions about your betting experience, bankroll, and objectives. Based on your answers, the assistant will offer tailored recommendations categorized as "lock," "risk," or "parlay", with hit probabilities and brief explanations.
# 
# If you leave the session, just rerun the notebook and enter the same username — the assistant will remember your previous conversation and pick up right where you left off. No need to re-answer onboarding questions unless you want to update your preferences.

# In[1]:


# Imports and Environment Setup

import os, json, requests, gradio as gr
from dotenv import load_dotenv
from datetime import datetime
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain.chat_models import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain.tools import BaseTool

_ = load_dotenv()
os.environ["ODDS_API_KEY"] = "3d27a8fe4087a6cf1696146cc50785af"
os.environ["TAVILY_API_KEY"] = "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"


# In[2]:


# History File Directory Setup

HISTORY_DIR = "/tmp/shared_betting_history"
os.makedirs(HISTORY_DIR, exist_ok=True)

try:
    os.chmod(HISTORY_DIR, 0o777)
except PermissionError:
    print(f"⚠️ Could not change permissions for {HISTORY_DIR}. You may need to set them manually.")


# In[3]:


# User History Load and Save Functions

def get_user_history_file(user_id):
    return os.path.join(HISTORY_DIR, f"{user_id}.json")

def load_user_history(user_id):
    path = get_user_history_file(user_id)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("chat_history", []), data.get("agent_scratchpad", [])
    return [], []

def save_user_history(user_id, chat_history, agent_scratchpad):
    path = get_user_history_file(user_id)
    with open(path, "w") as f:
        json.dump({
            "chat_history": [m.dict() for m in chat_history],
            "agent_scratchpad": agent_scratchpad,
        }, f, indent=2)
    os.chmod(path, 0o666)


# In[4]:


# NBA Odds Fetcher via The Odds API
class NBAOddsTool(BaseTool):
    name: str = "nba_odds_api"
    description: str = (
        "Use this to get current NBA game odds from The Odds API. Input is ignored."
    )

    def _run(self, _: str = "") -> str:
        api_key = os.getenv("ODDS_API_KEY")
        url = (
            "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
            f"?apiKey={api_key}&regions=us&markets=h2h,spreads,totals"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.text

    async def _arun(self, _: str):
        raise NotImplementedError


# In[5]:


# Tool Setup and System Prompt Definition

nba_odds_tool = NBAOddsTool()
tavily_tool = TavilySearchResults(k=5, time_period="day")
TOOLS = [nba_odds_tool, tavily_tool]

today = datetime.today().strftime("%B %d, %Y")
SYSTEM_PROMPT = f"""
GOAL:
You are a specialized AI assistant designed to function as a sports betting advisor, optimized specifically for NBA basketball betting. Your objective is to guide users by providing strategic betting recommendations categorized clearly as "lock," "risk," or "parlay," while actively adapting to the user's experience level, preferences, and bankroll.

TOOLS:
1. `nba_odds_api` – Use this to fetch current NBA betting lines (moneyline, spreads, totals, props) as of {today}.
2. `tavily_search_results_json` – Use this for fresh injury updates or team performance trends. Limit to results from the last 24 hours.

TOOL RULES:
• Always call `nba_odds_api` first when you need betting lines, odds, or totals.
• Only use `tavily_search_results_json` when looking for player injuries, hot streaks, or momentum narratives — and ignore anything over 24 hours old.
• If you present odds, always cite the date of retrieval, e.g., “(odds as of {today})”.
• If the odds tool returns nothing or fails, apologize and suggest trying again later.

PERSONA:
You play the role of a knowledgeable, clear, and supportive sports betting advisor who understands both novice and experienced bettors. You're confident in your recommendations, continuously aiming to improve the user's betting accuracy and profitability over time.

NARRATIVE:
Upon first interaction, introduce yourself and determine the user's level of betting experience. Then tailor your guidance to suit their level—providing simple explanations for beginners or detailed strategy for experienced users. Reference past user interactions to personalize future advice.

DIALOG FLOW:

STEP 1: GATHER INFORMATION  
You should do this:
• Greet the user and clearly state your role as a basketball betting assistant.  
• Ask these questions **one at a time**, waiting for responses:  
    1. Is this your first time betting, or are you an experienced bettor?  
    2. What specific betting goals do you have today (e.g., general strategy, advice on current bets, creating a parlay)?  
    3. What is your bankroll (how much money are you looking to bet)?  
    4. (If experienced) What specific betting strategies or types of bets (e.g., spreads, totals, player props) do you prefer?

• Wait for the user’s response before moving on.  
• Clarify their experience and intentions clearly before making recommendations.  
• **Do NOT** ask multiple questions at once or offer bets before gathering all required info.

STEP 2: BEGIN PROVIDING BETTING ADVICE, ADAPTING TO USER RESPONSES  
You should do this:
• Use `nba_odds_api` to pull live odds and matchups.  
• Use `tavily_search_results_json` only if needed for injury or performance trends.  
• Provide recommendations that balance value and risk—use player props, totals, and spreads appropriately.  
• Tailor advice based on experience:
    - **Beginners** → Use clear language, simple bet types (moneyline, spread), and define terms. If they ask for a parlay, offer a simple, high-EV version.
    - **Experienced users** → Ask how many legs they want in a parlay and provide the highest EV parlay for that length. Use more advanced strategy language and ask for preferences.

• Categorize each recommendation clearly as **"lock," "risk," or "parlay"**.
• Include estimated hit probabilities for each bet, with a short explanation.
• Put all bet suggestions in the context of their bankroll (e.g., “a $5 bet returns...”).
• If the user enters existing bets:
    - Analyze their likelihood of success.
    - Suggest improvements or alternate bet structures.
    - Propose additive bets or parlays to increase expected value.

• When users ask for “tonight’s best bets,” give a smart blend of **lock** and **risk** categories with the highest EV.
• When suggesting multiple bets, ask the user what mix they want (lock, risk, parlay) before proceeding.
• Frequently follow up with open-ended questions to keep users engaged and learning.

Don’t do this:
• Avoid vague advice or odds with no rationale.  
• Never ignore a user's previous betting history or current skill level.

STEP 3: ASSESS AND IMPROVE OVER TIME  
• Ask repeat users about their previous bets:  
   – Which won or lost?  
   – Do they want to try a similar strategy again or try something new?  
• Adapt your strategy based on that feedback.  
• Periodically reassess the user's betting experience and update complexity level accordingly.

STEP 4: WRAP UP  
• Clearly summarize all bet suggestions at the end of the session.  
• Invite the user to return before the next games or whenever lines are updated.
"""


# In[6]:


# Prompt Template and Agent Setup

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history"),
    MessagesPlaceholder("agent_scratchpad"),
    ("human", "{input}")
])

llm = ChatOpenAI(model="gpt-4o", temperature=0.3)
agent = create_openai_functions_agent(llm=llm, tools=TOOLS, prompt=prompt)
agent_executor = AgentExecutor(agent=agent, tools=TOOLS, verbose=True, handle_parsing_errors=True)

session_cache = {}


# In[7]:


# Return User Handling and Welcome Prompt

def welcome_user(user_id):
    chat, scratchpad = load_user_history(user_id)
    if not chat:
        return [], [], None

    last_msg = next((m["content"] for m in reversed(chat) if m["type"] == "ai"), None)
    if not last_msg:
        return [], [], None

    welcome_prompt = (
        f"The user has returned. Their last assistant message was:\n\"{last_msg}\"\n"
        f"Generate a warm welcome that references this bet and invites them to continue."
    )
    response = llm.invoke(welcome_prompt)
    return [HumanMessage(**m) if m["type"] == "human" else AIMessage(**m) for m in chat], scratchpad, response.content


# In[8]:


# Chat Function 

def chat_fn(message, user_id):
    if not user_id:
        return "Please enter your username to continue.", []

    if user_id not in session_cache:
        chat_history, scratchpad, welcome = welcome_user(user_id)
        display = [(None, welcome)] if welcome else []
        session_cache[user_id] = {
            "chat_history": chat_history,
            "agent_scratchpad": scratchpad,
            "display": display
        }

    session = session_cache[user_id]
    session["chat_history"].append(HumanMessage(content=message))

    response = agent_executor.invoke({
        "input": message,
        "chat_history": session["chat_history"],
        "agent_scratchpad": session["agent_scratchpad"]
    })

    session["chat_history"].append(AIMessage(content=response["output"]))
    session["display"].append((message, response["output"]))
    save_user_history(user_id, session["chat_history"], session["agent_scratchpad"])

    return "", session["display"]


# In[9]:


# Gradio UI Setup and Session Initialization When User Enters a Username

with gr.Blocks() as app:
    gr.Markdown("## 🏀 NBA Betting Assistant")

    user_input = gr.Textbox(label="Username", placeholder="e.g. matt24")
    chatbot = gr.Chatbot()
    message_input = gr.Textbox(label="Message", placeholder="Ask about tonight’s games…")
    send_btn = gr.Button("Send")

    def initialize_user(uid):
        if uid and uid not in session_cache:
            _, _, welcome = welcome_user(uid)
            if welcome:
                session_cache[uid] = {
                    "chat_history": [],
                    "agent_scratchpad": [],
                    "display": [(None, welcome)]
                }
                return [(None, welcome)]
            else:
                session_cache[uid] = {"chat_history": [], "agent_scratchpad": [], "display": []}
        return session_cache.get(uid, {}).get("display", [])

    user_input.change(initialize_user, inputs=user_input, outputs=chatbot)
    send_btn.click(chat_fn, inputs=[message_input, user_input], outputs=[message_input, chatbot])


# In[10]:


app.launch(share=True)

