import sys

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('initial_sidebar_state="collapsed"', 'initial_sidebar_state="expanded"')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('pages/1_Home.py', 'r', encoding='utf-8') as f:
    text = f.read()
text = text.replace('initial_sidebar_state="collapsed"', 'initial_sidebar_state="expanded"')
with open('pages/1_Home.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('pages/2_Tool.py', 'r', encoding='utf-8') as f:
    text = f.read()
text = text.replace('initial_sidebar_state="collapsed"', 'initial_sidebar_state="expanded"')
with open('pages/2_Tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
