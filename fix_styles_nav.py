import sys

with open('styles.css', 'r', encoding='utf-8') as f:
    css = f.read()

import re
# Hide the top bar from streamlit if it shows up empty
css += '''
/* Hide header completely now that we use sidebar */
[data-testid="stHeader"] {
    display: none !important;
}

/* Make block container go higher up */
.block-container {
    padding-top: 1.5rem !important;
}

/* Tweak sidebar */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--line) !important;
}

/* Make the sidebar links nicer */
[data-testid="stSidebarNav"] {
    display: none; /* Hide default streamlit nav if any */
}

a[data-testid="stPageLink"] {
    margin-bottom: 0.5rem;
}
'''

with open('styles.css', 'w', encoding='utf-8') as f:
    f.write(css)

