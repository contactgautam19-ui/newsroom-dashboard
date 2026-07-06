"""Production-format recipes for N-Pro.

Each recipe is fully declarative: the pre-generation questions the UI asks
(chips / text / multi-select / guest cards) and the generation instruction that
tells the model exactly what broadcast artefacts to produce. Adding or tuning a
format is a data edit here — the chat engine and UI are generic.
"""

# Shared output rules appended to every format instruction.
COMMON_RULES = (
    "Use plain text with SIMPLE UPPERCASE SECTION LABELS ending in a colon (e.g. "
    "HEADLINES:, ANCHOR READ:, WHY THIS MATTERS:). Do NOT use markdown (#, *, **). "
    "Ground every claim in the supplied reporting; if a fact is missing write "
    "\"details awaited\". Clearly mark anything unconfirmed as (UNVERIFIED). Keep "
    "sentences short and easy to read aloud on air."
)

RECIPES = {
    "av_read": {
        "label": "AV Read",
        "icon": "▶",
        "blurb": "Fast anchor read for bulletins.",
        "questions": [
            {"id": "duration", "type": "chips",
             "prompt": "How long should the anchor read run?",
             "options": ["20 seconds", "30 seconds", "45 seconds", "60 seconds"]},
        ],
        "instruction": (
            "Produce a bulletin AV READ for a TV anchor.\n"
            "Sections, in order:\n"
            "HEADLINES: three options, each MAX 40 characters, television-friendly "
            "(e.g. 'Air Crash Probe Begins').\n"
            "ANCHOR READ: a broadcast-ready read timed to {duration} — conversational, "
            "short sentences, no unnecessary adjectives, states clearly why the story "
            "matters.\n"
            "WHY THIS MATTERS: 2-3 concise sentences on viewer impact."
        ),
    },
    "package": {
        "label": "Package",
        "icon": "\U0001F4E6",
        "blurb": "Anchor intro + VOs + outcue.",
        "questions": [
            {"id": "tone", "type": "chips_custom",
             "prompt": "Choose a tone for the package.",
             "options": ["Neutral", "Sensible", "Serious", "Investigative",
                         "Human", "Emotional", "High Energy", "Sensational"],
             "custom_hint": "e.g. Write like WION / BBC / NDTV / CNBC"},
        ],
        "instruction": (
            "Produce a TV PACKAGE in a {tone} tone.\n"
            "Sections, in order:\n"
            "ANCHOR INTRODUCTION: 15-20 seconds.\n"
            "VOICE OVER 1:\nVOICE OVER 2:\nVOICE OVER 3:\n"
            "ANCHOR OUTCUE:\n"
            "HEADLINES: three options, each MAX 40 characters.\n"
            "POINTERS: three short label:value pointers (labels like KEY TAKEAWAY, "
            "BIG QUESTION, WHAT NEXT or IMPACT, FLASHPOINT, BOTTOM LINE).\n"
            "WHY THIS MATTERS: 2-3 sentences."
        ),
    },
    "explainer": {
        "label": "Primetime Explainer",
        "icon": "\U0001F3AF",
        "blurb": "Deep prime-time segment.",
        "questions": [
            {"id": "tone", "type": "chips_custom",
             "prompt": "Select a tone.",
             "options": ["Neutral", "Sensible", "Serious", "Investigative",
                         "High Energy", "Sensational", "Analytical"],
             "custom_hint": "Describe a custom tone"},
            {"id": "anchor_style", "type": "text",
             "prompt": "What style should the anchor use?",
             "placeholder": "Calm / Aggressive / Data-driven / Storytelling / Conversational"},
            {"id": "audience", "type": "text",
             "prompt": "Who is the primary audience, and what should they understand "
                       "after this segment?",
             "placeholder": "e.g. Urban prime-time viewers — grasp why the probe stalled"},
            {"id": "editorial", "type": "multi",
             "prompt": "Editorial choices for this explainer.",
             "options": ["Challenge official claims", "Include opposing viewpoints",
                         "Include a timeline", "Suggest graphics",
                         "Fact-heavy treatment", "Emotion-heavy treatment"]},
        ],
        "instruction": (
            "Act as an experienced primetime executive producer. Produce a PRIMETIME "
            "EXPLAINER in a {tone} tone, anchor style: {anchor_style}. Audience/goal: "
            "{audience}. Editorial choices: {editorial}.\n"
            "Sections, in order:\n"
            "HEADLINES: three options, each MAX 40 characters.\n"
            "ANCHOR INTRO:\n"
            "SEGMENT STRUCTURE: the beat-by-beat running order.\n"
            "VOICE OVERS: the scripted VO blocks.\n"
            "GRAPHICS SUGGESTIONS: specific GFX/lower-thirds to build.\n"
            "STATISTICS: the key numbers to put on screen.\n"
            "TIMELINE: dated chronology if relevant.\n"
            "KEY POINTERS: three label:value pointers.\n"
            "STRONG ENDING: the closing line.\n"
            "WHY THIS MATTERS: 2-3 sentences."
        ),
    },
    "debate": {
        "label": "Debate",
        "icon": "\U0001F5E3",
        "blurb": "Panel debate builder.",
        "questions": [
            {"id": "guests", "type": "guests",
             "prompt": "Who are the guests? Add each panellist.",
             "fields": ["Guest name", "Designation", "Affiliation (optional)",
                        "Area of expertise"]},
            {"id": "audience", "type": "chips",
             "prompt": "Who is the target audience?",
             "options": ["Urban", "Youth", "Business", "Political", "General", "Regional"]},
        ],
        "instruction": (
            "Build a television DEBATE for a {audience} audience with these guests:\n"
            "{guests}\n"
            "Sections, in order:\n"
            "OPENING STATEMENT:\nANCHOR FRAMING:\nDEBATE STRUCTURE:\n"
            "OPENING QUESTION:\nFOLLOW-UP QUESTIONS:\nSHARP COUNTER QUESTIONS:\n"
            "EVIDENCE-BASED CHALLENGE QUESTIONS:\nFACT-CHECK PROMPTS:\nCLOSING REMARKS:\n"
            "Questions must be short, sharp and fact-driven, addressed to the named "
            "guests where relevant. Challenge claims respectfully. Never defamatory; "
            "never generate personal attacks."
        ),
    },
    "custom": {
        "label": "Custom Script",
        "icon": "✍",
        "blurb": "Answer a few planning questions.",
        "questions": [
            {"id": "angle", "type": "text", "prompt": "What is the story angle?",
             "placeholder": "The angle you want to lead with"},
            {"id": "audience", "type": "text", "prompt": "Intended audience?",
             "placeholder": "e.g. general prime-time viewers"},
            {"id": "length", "type": "chips", "prompt": "How long should the script be?",
             "options": ["30 sec", "1 min", "2 min", "3 min+"]},
            {"id": "tone", "type": "text", "prompt": "What tone should it have?",
             "placeholder": "e.g. serious, analytical, human"},
            {"id": "include", "type": "multi", "prompt": "What should it include?",
             "options": ["History", "Expert voices", "Highlight data",
                         "Emphasize emotion", "Challenge official claims",
                         "Purely factual", "Graphics", "Maps",
                         "Social media reactions", "International comparisons"]},
        ],
        "instruction": (
            "Produce a CUSTOM broadcast script.\n"
            "Angle: {angle}. Audience: {audience}. Target length: {length}. "
            "Tone: {tone}. Include: {include}.\n"
            "Sections, in order:\n"
            "HEADLINES: three options, each MAX 40 characters.\n"
            "SCRIPT: the full script honouring every choice above, with clear "
            "ANCHOR / VO / GFX cues.\n"
            "WHY THIS MATTERS: 2-3 sentences."
        ),
    },
}

FORMAT_ORDER = ["av_read", "package", "explainer", "debate", "custom"]

# Smart action chips shown under a generated script -> transformation instruction.
SMART_ACTIONS = {
    "shorter": ("Rewrite shorter", "Rewrite the script noticeably shorter and tighter "
                "while keeping every verified fact and the on-air structure."),
    "conversational": ("More conversational", "Rewrite in a warmer, more conversational "
                       "anchor voice without losing accuracy."),
    "dramatic": ("More dramatic", "Rewrite with more on-air energy and urgency, but do "
                 "not exaggerate or add unverified claims."),
    "more_facts": ("Add more facts", "Weave in more of the verified facts and numbers "
                   "from the supplied reporting; do not invent any."),
    "history": ("Add historical context", "Add a short, accurate historical-context "
                "passage using the supplied reporting."),
    "graphics": ("Generate graphics", "List broadcast graphics/lower-thirds to build "
                 "for this story, each with the exact on-screen text."),
    "debate_qs": ("Debate questions", "Generate 8-10 short, sharp, fact-driven debate "
                  "questions for this story. Respectful, never defamatory."),
    "social": ("Social captions", "Write social captions: an X post (<280 chars), an "
               "Instagram caption with 3-5 hashtags, and a Facebook post."),
    "yt_title": ("YouTube title", "Write 5 punchy YouTube titles (max 70 chars) for "
                 "this story, no clickbait falsehoods."),
    "thumbnail": ("Thumbnail text", "Write 5 short thumbnail text overlays (2-4 words "
                  "each) for this story."),
    "hindi": ("Translate to Hindi", "Translate the script into natural broadcast Hindi, "
              "keeping the section labels."),
    "english": ("Translate to English", "Translate the script into natural broadcast "
                "English, keeping the section labels."),
    "digital": ("Rewrite for digital", "Rewrite as a 400-600 word digital news article "
                "with a web headline and sub-headline."),
    "ott": ("Rewrite for OTT", "Rewrite as a tighter, streaming/OTT-style narrated "
            "script with scene cues."),
}
