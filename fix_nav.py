import sys

with open('_shared.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace the top navigation with a sidebar navigation
sidebar_nav = '''
    with st.sidebar:
        st.markdown('<div class="top-nav-brand" style="font-size: 1.5rem; margin-bottom: 2rem;">Trova Prezzi Mio</div>', unsafe_allow_html=True)
        
        st.page_link("pages/1_Home.py", label="Home", icon="??")
        st.page_link("pages/2_Tool.py", label="Ricerca", icon="??")
        
        st.markdown("---")
        
        if st.selectbox(
            "Tema della UI", 
            ["Dark", "Light"], 
            index=0 if theme_mode == "dark" else 1,
            key="theme_selector",
            help="Scegli il tema dell'applicazione"
        ) == "Dark":
            if st.session_state.get("ui_theme") != "dark":
                st.session_state["ui_theme"] = "dark"
                st.rerun()
        else:
            if st.session_state.get("ui_theme") != "light":
                st.session_state["ui_theme"] = "light"
                st.rerun()
'''

import re
text = re.sub(r'st\.markdown\(.*?<div class="top-nav">.*?</script>"\n\s*\)', '', text, flags=re.DOTALL)

with open('_shared.py', 'w', encoding='utf-8') as f:
    f.write(text.replace('def render_nav(active_page: str = "tool"):', 'def render_nav(active_page: str = "tool"):\n' + sidebar_nav))

