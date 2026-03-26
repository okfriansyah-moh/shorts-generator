"""Template pool for hook generation.

Contains 30+ parameterised hook and story templates. Templates are
selected via deterministic rotation (hash-based index), never random.
"""

from __future__ import annotations

# Each template is a (hook_template, story_template) pair.
# Placeholders: {subject}, {action}, {adjective}
# Fallback templates use no placeholders and work without transcript.

HOOK_TEMPLATES: tuple[tuple[str, str], ...] = (
    # --- Excitement / hype ---
    (
        "You won't believe this {adjective} {subject} play",
        "Watch this {adjective} moment unfold in the most unexpected way",
    ),
    (
        "This {subject} moment is absolutely {adjective}",
        "The gameplay takes a wild turn that nobody saw coming",
    ),
    (
        "Wait for this {adjective} {subject} moment",
        "One of the most intense sequences you will ever see",
    ),
    (
        "The most {adjective} {subject} play ever",
        "This clip proves that anything can happen in gaming",
    ),
    (
        "How did this {adjective} {subject} happen",
        "The odds of this happening are one in a million",
    ),
    # --- Question / curiosity ---
    (
        "Did you see that {adjective} {subject}",
        "This moment had everyone watching in disbelief",
    ),
    (
        "Can you believe this {adjective} play",
        "The reactions to this moment were absolutely priceless",
    ),
    (
        "Who else caught this {adjective} {subject}",
        "This is the kind of moment that makes gaming special",
    ),
    (
        "What just happened with this {subject}",
        "Sometimes the game gives you moments like this",
    ),
    (
        "Is this the best {subject} play",
        "Watching this will make your jaw drop to the floor",
    ),
    # --- Command / engagement ---
    (
        "Watch this {adjective} {subject} closely",
        "Pay attention because this happens in the blink of an eye",
    ),
    (
        "Check out this {adjective} {subject} move",
        "A display of skill that deserves to be watched on repeat",
    ),
    (
        "Stop scrolling for this {adjective} play",
        "This is the content you have been searching for today",
    ),
    (
        "Drop everything and watch this {subject}",
        "This moment right here is why we love gaming so much",
    ),
    (
        "You need to see this {adjective} moment",
        "Trust me this is one clip you do not want to miss",
    ),
    # --- Story / narrative ---
    (
        "This {adjective} {subject} changed everything",
        "From that point on nothing was ever the same again",
    ),
    (
        "The moment this {subject} went {adjective}",
        "It started like any other round but then it happened",
    ),
    (
        "When the {subject} gets too {adjective}",
        "This is what peak gaming performance actually looks like",
    ),
    (
        "Here is why this {subject} went viral",
        "Everyone has been talking about this incredible clip",
    ),
    (
        "That time the {subject} was absolutely {adjective}",
        "A truly unforgettable gaming moment caught on camera",
    ),
    # --- Reaction / emotional ---
    (
        "My jaw dropped at this {adjective} {subject}",
        "This is the kind of play you have to watch twice",
    ),
    (
        "I cannot stop watching this {subject} play",
        "Some moments are so good they deserve an instant replay",
    ),
    (
        "This {adjective} {subject} had me screaming",
        "The intensity of this moment is hard to put into words",
    ),
    (
        "This {subject} moment hits different",
        "There is something special about plays like this one",
    ),
    (
        "Only real gamers understand this {subject}",
        "If you know you know and this clip proves it",
    ),
    # --- Superlative / ranking ---
    (
        "Top tier {adjective} {subject} right here",
        "This belongs in the hall of fame of gaming clips",
    ),
    (
        "One of the best {subject} plays this year",
        "Clips like this are what make the highlights reel",
    ),
    (
        "This {adjective} play deserves more views",
        "Share this with someone who appreciates great gameplay",
    ),
    (
        "Peak {adjective} {subject} performance",
        "When everything comes together for the perfect play",
    ),
    (
        "The definition of a {adjective} {subject} play",
        "Textbook execution from start to finish in this clip",
    ),
    # --- Extra templates to reach 30+ ---
    (
        "This {adjective} {subject} will blow your mind",
        "Prepare yourself because this clip is on another level",
    ),
    (
        "No way this {subject} just happened",
        "Sometimes the game creates moments you cannot script",
    ),
    (
        "POV you witness a {adjective} {subject}",
        "Step into the action and experience this for yourself",
    ),
)

# Fallback templates for clips with no transcript text
FALLBACK_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "You need to see this incredible moment",
        "Watch this gameplay clip that everyone is talking about",
    ),
    (
        "This gaming moment is absolutely unreal",
        "Sometimes the best plays happen when you least expect them",
    ),
    (
        "Wait for the best part of this clip",
        "The highlight of this gameplay will leave you speechless",
    ),
    (
        "One of the wildest gaming clips ever",
        "This is the type of content that makes shorts addictive",
    ),
    (
        "Stop and watch this insane play",
        "A moment so good it has to be seen to be believed",
    ),
)

# Adjective pool for template filling when no good keywords exist
DEFAULT_ADJECTIVES: tuple[str, ...] = (
    "insane",
    "incredible",
    "unreal",
    "clutch",
    "epic",
    "legendary",
    "wild",
    "crazy",
    "intense",
    "massive",
)

# Subject pool for template filling when no good keywords exist
DEFAULT_SUBJECTS: tuple[str, ...] = (
    "gameplay",
    "moment",
    "play",
    "action",
    "highlight",
)
