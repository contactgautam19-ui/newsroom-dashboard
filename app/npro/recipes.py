"""Production-format recipes for N-Pro.

Each recipe is fully declarative: the pre-generation questions the UI asks
(chips / text / multi-select / guest cards) and the generation instruction that
tells the model exactly what broadcast artefacts to produce. Adding or tuning a
format is a data edit here — the chat engine and UI are generic.

The voice and the hard rules (no outlet names, nothing beyond the reporting)
live in app/npro/house.py; the sections each format must contain are declared
there too (house.FORMATS) and are what a finished script is checked against.
"""

# Shared output rules appended to every format instruction.
COMMON_RULES = (
    "Write the sections listed above, in that order, each label in UPPERCASE "
    "ending with a colon on its own line, then finish with PRODUCER NOTES:. "
    "Plain text only. HEADLINES are three options, one per line; the screen "
    "takes 40 characters including spaces, so aim for 36 or fewer. No outlet names anywhere. If the "
    "reporting does not support a section, keep it to what is known rather "
    "than padding it."
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
            "Write a bulletin AV READ for a television anchor.\n"
            "Sections, in order:\n"
            "HEADLINES: three options.\n"
            "ANCHOR READ: timed to {duration} at an anchor's pace (about 2.5 "
            "words a second). Lead with the main development, add the single "
            "most important piece of context, say why it matters, and close on "
            "what happens next or the question still open. One angle only.\n"
            "WHY THIS MATTERS: two or three sentences on what this means for "
            "viewers."
        ),
    },
    "package": {
        "label": "Package",
        "icon": "\U0001F4E6",
        "blurb": "Anchor intro, VOs, outcue.",
        "questions": [
            {"id": "tone", "type": "chips_custom",
             "prompt": "Choose a tone for the package.",
             "options": ["Neutral", "Sensible", "Serious", "Investigative",
                         "Human", "Emotional", "High Energy", "Sensational"],
             "custom_hint": "Describe the tone you want, e.g. calm and analytical"},
        ],
        "instruction": (
            "Write a television PACKAGE in a {tone} tone.\n"
            "Sections, in order:\n"
            "ANCHOR INTRO: 15 to 20 seconds; the development and why it matters.\n"
            "VO 1: the core facts and immediate context.\n"
            "VO 2: the next most important angle, evidence or consequence.\n"
            "VO 3: the wider significance or a competing interpretation. Add "
            "VO 4 only if there is a response or next step worth its own beat; "
            "leave out any VO the reporting cannot fill.\n"
            "SOT: include only if a source carries a direct quotation; give the "
            "speaker and the exact words. Otherwise leave the section out.\n"
            "OUTCUE: one clean, factual line that moves the story forward.\n"
            "HEADLINES: three options.\n"
            "POINTERS: three short pointers for the screen, one per line, each "
            "starting with a dash, in the form 'Key takeaway - ...', 'Big "
            "question - ...', 'What next - ...'.\n"
            "WHY THIS MATTERS: two or three sentences.\n"
            "Narration complements pictures; do not describe what the viewer "
            "can already see."
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
            "Write a PRIMETIME EXPLAINER as an experienced executive producer "
            "would. Tone: {tone}. Anchor style: {anchor_style}. Audience and "
            "goal: {audience}. Editorial choices: {editorial}.\n"
            "Sections, in order:\n"
            "HEADLINES: three options.\n"
            "ANCHOR INTRO: open on the central question or tension.\n"
            "WHAT HAPPENED: the development, plainly.\n"
            "HOW WE GOT HERE: only the background the sources give.\n"
            "KEY EVIDENCE: the figures, decisions and turning points, and why "
            "each matters.\n"
            "WHAT IT COULD MEAN: consequences, clearly marked as interpretation.\n"
            "COUNTERPOINTS: other readings or responses found in the sources.\n"
            "WHAT REMAINS UNCERTAIN: what is not yet known or confirmed.\n"
            "WHAT TO WATCH NEXT: the next decision, deadline or response.\n"
            "Add a TIMELINE: section (dated lines) and a GFX: section (exact "
            "on-screen text) after WHAT TO WATCH NEXT only if the editorial "
            "choices ask for them and the sources supply the material.\n"
            "Build the argument from evidence. Do not repeat the same point in "
            "more than one section."
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
            "Build a television DEBATE for a {audience} audience with these "
            "panellists:\n{guests}\n"
            "Sections, in order:\n"
            "ANCHOR OPEN: the development and the core dispute.\n"
            "WHY THIS MATTERS: the stakes, in a few lines.\n"
            "CENTRAL DEBATE QUESTION: one question that does not assume its "
            "answer.\n"
            "QUESTIONS FOR PANELISTS: six to eight short, specific questions "
            "that test evidence, reasoning, accountability and consequences, "
            "each addressed to a named panellist where one fits.\n"
            "COUNTERPOINTS: credible alternative readings, or evidence in the "
            "sources that challenges the dominant framing.\n"
            "FACT-CHECK PROMPTS: three or four facts from the sources the "
            "anchor can use to hold a panellist to the record.\n"
            "CLOSING QUESTION: the decision or unresolved issue to leave "
            "viewers with.\n"
            "Never treat an allegation as established inside a question. No "
            "false balance between positions with very different evidence. "
            "Never defamatory, never personal. Say nothing about a panellist "
            "beyond what the editor supplied."
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
            "Write a CUSTOM broadcast script.\n"
            "Angle: {angle}. Audience: {audience}. Target length: {length}. "
            "Tone: {tone}. Include: {include}.\n"
            "Sections, in order:\n"
            "HEADLINES: three options.\n"
            "SCRIPT: the full script honouring every choice above. Mark turns "
            "inside it with (ANCHOR), (VO) and (GFX) in round brackets at the "
            "start of a line, not as separate sections. Include an element the "
            "editor asked for only where the sources supply the material, and "
            "say in PRODUCER NOTES which requested elements could not be "
            "covered from the reporting.\n"
            "WHY THIS MATTERS: two or three sentences."
        ),
    },
}

FORMAT_ORDER = ["av_read", "package", "explainer", "debate", "custom"]

# Smart action chips shown under a generated script -> transformation instruction.
SMART_ACTIONS = {
    "shorter": ("Rewrite shorter", "Rewrite the script noticeably shorter and tighter "
                "while keeping every supported fact and the on-air structure."),
    "conversational": ("More conversational", "Rewrite in a warmer, more conversational "
                       "anchor voice without losing accuracy."),
    "dramatic": ("More dramatic", "Rewrite with more on-air energy and urgency, but do "
                 "not exaggerate or add anything the reporting does not support."),
    "more_facts": ("Add more facts", "Weave in more of the facts and figures from the "
                   "supplied sources; do not add any from elsewhere."),
    "history": ("Add historical context", "Add a short historical-context passage "
                "using only background found in the supplied sources. If the "
                "sources carry none, say so in PRODUCER NOTES and leave the "
                "script as it is."),
    "graphics": ("Generate graphics", "List broadcast graphics and lower-thirds to build "
                 "for this story, each with the exact on-screen text."),
    "debate_qs": ("Debate questions", "Write eight to ten short, sharp, fact-driven debate "
                  "questions on this story. Respectful, never defamatory."),
    "social": ("Social captions", "Write social captions: an X post (under 280 characters), "
               "an Instagram caption with three to five hashtags, and a Facebook post."),
    "yt_title": ("YouTube title", "Write five punchy YouTube titles (70 characters at "
                 "most) for this story. Nothing the reporting does not support."),
    "thumbnail": ("Thumbnail text", "Write five short thumbnail text overlays (two to "
                  "four words each) for this story."),
    "hindi": ("Translate to Hindi", "Translate the script into natural broadcast Hindi, "
              "keeping the section labels in English and every figure unchanged."),
    "english": ("Translate to English", "Translate the script into natural broadcast "
                "English, keeping the section labels and every figure unchanged."),
    "digital": ("Rewrite for digital", "Rewrite as a 400 to 600 word digital news article "
                "with a web headline and a sub-headline."),
    "ott": ("Rewrite for OTT", "Rewrite as a tighter, streaming-style narrated script "
            "with scene cues."),
}
