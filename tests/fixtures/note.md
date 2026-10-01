---
tags: [net, lezione]
---
# Lezione 1 — Firewall

## Firewall stateless e stateful

Un firewall **stateless** valuta ogni pacchetto in modo isolato, usando solo le regole su indirizzi e porte. Un firewall **stateful** invece tiene traccia delle connessioni[^1].

- Vantaggio dello stateful: blocca i pacchetti che non appartengono a nessuna connessione aperta.
- Svantaggio: [?]

## DMZ

La DMZ è una rete intermedia tra la rete interna e Internet, dove si mettono i server esposti (web, mail). Vedi anche [[VPN]].

[^1]: Tabella delle connessioni, detta anche *state table*.
