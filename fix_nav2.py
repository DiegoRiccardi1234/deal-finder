import sys

with open('_shared.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace the render_nav function entirely
new_render_nav = '''
def render_nav(active_page: str = "tool"):
    theme_mode = str(st.session_state.get("ui_theme", "dark")).strip().lower()
    
    with st.sidebar:
        st.markdown('<div class="top-nav-brand" style="font-size: 1.6rem; margin-bottom: 2rem; margin-top: 1rem;">Trova Prezzi Mio</div>', unsafe_allow_html=True)
        
        st.page_link("pages/1_Home.py", label="Home Page" if active_page != "home" else "?? Home Page", icon="??")
        st.page_link("pages/2_Tool.py", label="Cerca Offerte" if active_page != "tool" else "?? Cerca Offerte", icon="??")
        
        st.markdown("<br><br><br><br><br><br><br><br>", unsafe_allow_html=True)
        st.markdown("---")
        
        # Theme selector in sidebar
        new_theme = st.selectbox(
            "Tema Interfaccia", 
            ["Dark", "Light"], 
            index=0 if theme_mode == "dark" else 1,
            key="theme_selector"
        )
        
        if new_theme == "Dark" and theme_mode != "dark":
            st.session_state["ui_theme"] = "dark"
            st.rerun()
        elif new_theme == "Light" and theme_mode != "light":
            st.session_state["ui_theme"] = "light"
            st.rerun()
        
        st.markdown('<div style="margin-top: 1rem; font-size: 0.8rem; color: gray;">&copy; 2026 Trova Prezzi Mio</div>', unsafe_allow_html=True)
'''

import re
# Find the render_nav def and replace to the end
text = re.sub(r'def render_nav\(active_page: str = "tool"\):.*$', new_render_nav, text, flags=re.DOTALL)

with open('_shared.py', 'w', encoding='utf-8') as f:
    f.write(text)
