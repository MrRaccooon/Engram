"""
Seed concept vocabulary for zero-shot CLIP tagging.

The list is intentionally broad and generated from curated primitives so the
initial concept space is large without hand-writing hundreds of prompts.
"""

from __future__ import annotations


_CATEGORIES: dict[str, list[str]] = {
    "code_editors": [
        "a screenshot of a code editor with source code",
        "a screenshot of python code in a dark theme editor",
        "a screenshot of javascript code in an IDE",
        "a screenshot of a code editor with syntax highlighting",
        "a screenshot of a code diff or patch view",
        "a screenshot of a debugging session in an IDE",
        "a screenshot of an IDE with file explorer sidebar",
        "a screenshot of a code review with comments",
        "a screenshot of test output in an IDE panel",
        "a screenshot of red error underlines in code",
        "a screenshot of a stack trace in an editor",
        "a screenshot of multiple editor tabs open",
        "a screenshot of VS Code or Cursor editor",
        "a screenshot of an integrated development environment",
        "a screenshot of code with a terminal panel below",
    ],
    "terminals": [
        "a screenshot of a terminal with command line output",
        "a screenshot of a powershell terminal window",
        "a screenshot of a shell prompt waiting for input",
        "a screenshot of terminal output with an error message",
        "a screenshot of terminal output showing tests running",
        "a screenshot of terminal output showing package installation",
        "a screenshot of a build log in terminal",
        "a screenshot of a terminal with git commands",
        "a screenshot of a terminal showing docker logs",
        "a screenshot of a terminal with python traceback",
        "a screenshot of a terminal with npm error",
        "a screenshot of a black terminal with green text",
    ],
    "browsers": [
        "a screenshot of a web browser with a webpage",
        "a screenshot of search results in Google",
        "a screenshot of online documentation in a browser",
        "a screenshot of a github repository page",
        "a screenshot of a stack overflow question",
        "a screenshot of a login page in a browser",
        "a screenshot of a web dashboard",
        "a screenshot of a settings page in browser",
        "a screenshot of a web form",
        "a screenshot of a browser error page",
        "a screenshot of a browser with multiple tabs",
        "a screenshot of a data chart in a web page",
    ],
    "communication": [
        "a screenshot of a chat application with messages",
        "a screenshot of a whatsapp conversation on screen",
        "a screenshot of a discord server chat",
        "a screenshot of a slack workspace with channels",
        "a screenshot of an email inbox",
        "a screenshot of a calendar with meetings",
        "a screenshot of a video call with webcam feeds",
        "a screenshot of a messaging app with notifications",
        "a screenshot of a group chat conversation",
        "a screenshot of a direct message window",
        "a screenshot of notification popups",
        "a screenshot of composing an email",
    ],
    "social_media": [
        "a screenshot of a social media feed with posts",
        "a screenshot of an instagram feed with photos",
        "a screenshot of a twitter timeline with tweets",
        "a screenshot of a reddit thread with comments",
        "a screenshot of youtube with video thumbnails",
        "a screenshot of comments on social media",
        "a screenshot of a short video feed like TikTok",
        "a screenshot of a social media profile page",
        "a screenshot of a social media story",
        "a screenshot of social media notifications",
    ],
    "productivity": [
        "a screenshot of a document editor with text",
        "a screenshot of a note-taking application",
        "a screenshot of a to-do list application",
        "a screenshot of a kanban board with cards",
        "a screenshot of a project management board",
        "a screenshot of a spreadsheet with data in cells",
        "a screenshot of a presentation slide editor",
        "a screenshot of a whiteboard with drawings",
        "a screenshot of a file manager window",
        "a screenshot of a task tracker dashboard",
        "a screenshot of a timeline planning view",
        "a screenshot of a markdown editor",
    ],
    "media": [
        "a screenshot of a video player with controls",
        "a screenshot of a music player application",
        "a screenshot of a photo gallery grid",
        "a screenshot of an image editing application",
        "a screenshot of a streaming platform like Netflix",
        "a screenshot of a paused video with progress bar",
        "a screenshot of an audio waveform editor",
        "a screenshot of a movie with subtitles",
        "a screenshot of a fullscreen video player",
        "a screenshot of a music playlist",
    ],
    "shopping": [
        "a screenshot of an ecommerce product listing",
        "a screenshot of a product details page with price",
        "a screenshot of an online shopping cart",
        "a screenshot of a checkout and payment page",
        "a screenshot of credit card payment form",
        "a screenshot of an order history page",
        "a screenshot of a product comparison table",
        "a screenshot of an online marketplace",
        "a screenshot of a food delivery app",
        "a screenshot of a travel booking page",
    ],
    "finance": [
        "a screenshot of a finance dashboard with numbers",
        "a screenshot of a banking website",
        "a screenshot of a payment confirmation",
        "a screenshot of an expense tracking app",
        "a screenshot of a stock market chart",
        "a screenshot of a crypto exchange with prices",
        "a screenshot of a budget spreadsheet",
        "a screenshot of an invoice document",
        "a screenshot of billing settings page",
        "a screenshot of a transaction history table",
    ],
    "gaming": [
        "a screenshot of a video game being played",
        "a screenshot of a game launcher with game tiles",
        "a screenshot of a game pause menu",
        "a screenshot of a game settings screen",
        "a screenshot of a live game stream on Twitch",
        "a screenshot of a game inventory screen",
        "a screenshot of a multiplayer game lobby",
        "a screenshot of a game world map",
    ],
    "design": [
        "a screenshot of a figma design canvas with UI elements",
        "a screenshot of a figjam whiteboard",
        "a screenshot of a UI mockup with buttons and text",
        "a screenshot of a component library in a design tool",
        "a screenshot of a color palette panel",
        "a screenshot of typography settings",
        "a screenshot of a prototyping flow with arrows",
        "a screenshot of a design review with annotations",
        "a screenshot of an artboard with grid guides",
        "a screenshot of a vector graphics editor",
    ],
    "documents": [
        "a screenshot of a PDF document with text",
        "a screenshot of a research paper with citations",
        "a screenshot of a technical specification document",
        "a screenshot of a legal document with clauses",
        "a screenshot of meeting notes",
        "a screenshot of a README markdown file",
        "a screenshot of a changelog document",
        "a screenshot of an instruction manual",
        "a screenshot of a report with headings and paragraphs",
        "a screenshot of a news article being read",
    ],
    "system": [
        "a screenshot of operating system settings",
        "a screenshot of a file open dialog box",
        "a screenshot of a save dialog box",
        "a screenshot of a system notification popup",
        "a screenshot of a software update prompt",
        "a screenshot of a permission dialog",
        "a screenshot of network settings panel",
        "a screenshot of bluetooth devices list",
        "a screenshot of battery settings",
        "a screenshot of a printer dialog",
    ],
    "visual_content": [
        "a photo of a person's face on screen",
        "a photo of a group of people on screen",
        "a selfie photo displayed on screen",
        "a family photo on a screen",
        "a photo of food on a plate on screen",
        "a photo of a pet on screen",
        "a landscape travel photo on screen",
        "a city street photo on screen",
        "a meme image on screen",
        "a chart or graph data visualization",
        "an infographic with icons and statistics",
        "a handwritten note scanned on screen",
    ],
    "errors_warnings": [
        "a screenshot of an error message dialog box",
        "a screenshot of a warning notification banner",
        "a screenshot of a failed build with red errors",
        "a screenshot of a crash report window",
        "a screenshot of a 404 page not found error",
        "a screenshot of a 500 server error page",
        "a screenshot of a permission denied error",
        "a screenshot of a connection timeout error",
        "a screenshot of syntax errors underlined in red",
        "a screenshot of failed test results",
        "a screenshot of a red alert warning banner",
    ],
    "activities": [
        "a screenshot of someone typing in a text field",
        "a screenshot of someone scrolling through content",
        "a screenshot of a modal dialog popup",
        "a screenshot of a search bar with a typed query",
        "a screenshot of a form being filled out",
        "a screenshot of a loading spinner",
        "a screenshot of a drag and drop interface",
        "a screenshot of clipboard paste operation",
        "a screenshot of switching between tabs",
        "a screenshot of a file upload progress bar",
        "a screenshot of screen sharing in progress",
        "a screenshot of an idle desktop with no apps",
    ],
}

_APP_NAMES = [
    "Visual Studio Code",
    "Cursor",
    "IntelliJ IDEA",
    "PyCharm",
    "Windows Terminal",
    "PowerShell",
    "Chrome",
    "Edge",
    "Firefox",
    "Brave",
    "Slack",
    "Discord",
    "WhatsApp",
    "Telegram",
    "Notion",
    "Obsidian",
    "Figma",
    "FigJam",
    "GitHub",
    "Linear",
    "Jira",
    "Excel",
    "Google Docs",
    "YouTube",
]

_APP_TEMPLATES = [
    "a screenshot of {} application",
    "a screenshot of {} window with content",
    "a screenshot of {} being used",
]


def _expand_app_prompts() -> list[str]:
    out: list[str] = []
    for app in _APP_NAMES:
        for tpl in _APP_TEMPLATES:
            out.append(tpl.format(app))
    return out


def get_seed_concepts() -> dict[str, list[str]]:
    """
    Return categorized seed concepts.
    Includes generated app-level concepts for broad cold-start coverage.
    """
    categories = {k: list(v) for k, v in _CATEGORIES.items()}
    categories["apps_generic"] = _expand_app_prompts()
    return categories


def all_seed_prompts() -> list[str]:
    """Return deduplicated seed prompts preserving insertion order."""
    seen: set[str] = set()
    prompts: list[str] = []
    for values in get_seed_concepts().values():
        for p in values:
            s = p.strip()
            if s and s not in seen:
                seen.add(s)
                prompts.append(s)
    return prompts

