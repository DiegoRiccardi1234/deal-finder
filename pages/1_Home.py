import streamlit as st
import os

from _shared import load_css, render_nav, get_theme_mode

st.set_page_config(page_title="Trova Prezzi Mio", page_icon="🔍", layout="wide", initial_sidebar_state="expanded")
load_css(theme_mode=get_theme_mode())
render_nav(active_page="home")

# Hero section
html = '<div class="hero-shell">'
html += '<div class="hero-kicker">\u2728 LA TUA RICERCA POTENZIATA</div>'
html += '<h1 class="hero-title">Risparmia sulle offerte tech<br><span class="hero-accent">senza sforzo</span></h1>'
html += '<p class="hero-copy">Il motore di comparazione prezzi che unisce la ricerca su 7 e-commerce e ti suggerisce il laptop o lo smartphone ideale, risparmiando tempo e denaro.</p>'
html += '<div class="hero-cta-row">'
html += '<a class="hero-cta-primary" href="/Tool" target="_self">Cerca un prodotto \u2192</a>'
html += '<a class="hero-cta-secondary" href="#come-funziona" target="_self">Scopri come funziona</a>'
html += '</div>'
html += '</div>'
st.markdown(html, unsafe_allow_html=True)

# Feature Cards
cards_html = '<div class="feature-cards-row">'
cards_html += '<div class="feature-card"><h3>\u26a1 Risparmio Cinetico</h3><p>Trova il prezzo pi\u00f9 basso istantaneamente consultando contemporaneamente 7 fonti affidabili in parallelo.</p></div>'
cards_html += '<div class="feature-card"><h3>\U0001f916 Verdetto AI</h3><p>L\u0027Intelligenza Artificiale analizza le specifiche e ti consiglia in base alle tue reali esigenze e budget.</p></div>'
cards_html += '<div class="feature-card"><h3>\U0001f3c6 Venditori Top</h3><p>Filtriamo i risultati mostrando solo venditori con ottimi feedback e garanzie d\u0027acquisto reali.</p></div>'
cards_html += '</div>'
st.markdown(cards_html, unsafe_allow_html=True)

# Source strip
strip_html = '<div class="source-strip">'
strip_html += '<span class="source-strip-label">Cerchiamo su</span>'
strip_html += '<span>Amazon</span><span>eBay</span><span>Trovaprezzi</span><span>Unieuro</span><span>MediaWorld</span><span>Euronics</span><span>Vinted</span>'
strip_html += '</div>'
st.markdown(strip_html, unsafe_allow_html=True)

# Come Funziona
how_html = '<div class="how-shell">'
how_html += '<div class="how-hero"><div class="how-hero-inner">'
how_html += '<span class="how-kicker">SEMPLICE E VELOCE</span>'
how_html += '<h2>Come funziona Trova Prezzi Mio</h2>'
how_html += '<p>Abbiamo semplificato la ricerca dell\u0027offerta perfetta in 4 semplici passaggi che puoi fare in pochi minuti.</p>'
how_html += '</div></div>'

how_html += '<div class="how-step">'
how_html += '<div class="how-step-copy"><div class="how-step-num">01</div><h4>Descrivi cosa ti serve</h4><p>Non sai che modello cercare? Chiedi all\u0027AI di Trova Prezzi Mio di suggerirti le specifiche in base all\u0027uso che devi farne.</p></div>'
how_html += '<div class="how-card"><div class="how-card-icon">\U0001f916</div><div class="how-card-label">Chat AI integrata</div><div class="how-card-sub">Scrivi la tua esigenza in linguaggio naturale</div></div>'
how_html += '</div>'

how_html += '<div class="how-step">'
how_html += '<div class="how-step-copy"><div class="how-step-num">02</div><h4>Ricerca Intelligente</h4><p>Il tool scansiona contemporaneamente Amazon, eBay, Unieuro, MediaWorld, Euronics e altri negozi italiani per estrarre le offerte.</p></div>'
how_html += '<div class="how-card"><div class="how-card-icon">\U0001f50d</div><div class="how-card-label">7 Store in parallelo</div><div class="how-card-sub">Risultati in pochi secondi</div></div>'
how_html += '</div>'

how_html += '<div class="how-step">'
how_html += '<div class="how-step-copy"><div class="how-step-num">03</div><h4>Confronto e Filtri</h4><p>Visualizza una tabella comparativa chiara con i prezzi migliori, ordina per convenienza e nascondi le offerte irrilevanti.</p></div>'
how_html += '<div class="how-card"><div class="how-card-icon">\U0001f4ca</div><div class="how-card-label">Tabella Comparativa</div><div class="how-card-sub">Ordina, filtra, nasconde i duplicati</div></div>'
how_html += '</div>'

how_html += '<div class="how-step">'
how_html += '<div class="how-step-copy"><div class="how-step-num">04</div><h4>Acquisto Sicuro</h4><p>Clicca sul link e procedi all\u0027acquisto direttamente dallo store ufficiale o esporta in CSV per decidere dopo.</p></div>'
how_html += '<div class="how-card"><div class="how-card-icon">\U0001f6d2</div><div class="how-card-label">Link Diretti & Export CSV</div><div class="how-card-sub">Vai allo store con un click</div></div>'
how_html += '</div>'

how_html += '<div class="how-cta">'
how_html += '<h3>Inizia a risparmiare ora</h3>'
how_html += '<p>Cerca il tuo prossimo prodotto tech e confronta i prezzi in tempo reale su 7 store italiani.</p>'
how_html += '<a class="hero-cta-primary" href="/Tool" target="_self">Apri il Tool \u2192</a>'
how_html += '</div>'
how_html += '</div>'

st.markdown(how_html, unsafe_allow_html=True)

# Footer
footer_html = '<div class="app-footer">'
footer_html += '<div>&copy; 2026 Trova Prezzi Mio. Tutti i diritti riservati.</div>'
footer_html += '<div class="app-footer-links"><span>Privacy</span><span>Termini</span></div>'
footer_html += '</div>'
st.markdown(footer_html, unsafe_allow_html=True)
