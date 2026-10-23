"""Mail récap (HTML compatible clients mail) et alerte en cas d’échec."""
import os
import smtplib
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from bot.spotify import SpotifyTokenExpired


GREEN, INK, MUTED, LINE, BG = "#1DB954", "#191414", "#6a6a6a", "#ececec", "#f4f4f4"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
BADGES = {"single": ("Single", "#e8f8ee", "#137a3a"), "album": ("Album", "#eef0ff", "#3a46a8"),
          "deluxe": ("Deluxe", "#fff4e0", "#8a5a00"), "feat": ("Feat", "#fdeef5", "#a3205e")}


def section(title, subtitle=""):
    sub = f'<div style="font-size:13px;color:{MUTED};margin-top:2px;">{subtitle}</div>' if subtitle else ""
    return (f'<tr><td style="padding:28px 24px 8px;"><div style="font-size:18px;font-weight:700;color:{INK};">'
            f'{title}</div>{sub}</td></tr>')


def row(cover, title, line2, url, badge=None, extra=""):
    """Une ligne : pochette 56px, titre, sous-titre, badge, lien Spotify."""
    img = (f'<img src="{escape(cover)}" width="56" height="56" alt="" '
           f'style="display:block;border-radius:6px;border:0;">') if cover else ""
    tag = ""
    if badge:
        label, bg, fg = BADGES[badge]
        tag = (f'<span style="display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;'
               f'border-radius:10px;background:{bg};color:{fg};margin-left:6px;vertical-align:middle;">{label}</span>')
    return (f'<tr><td style="padding:6px 24px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;">'
            f'<tr><td width="68" valign="top" style="width:68px;padding-right:12px;"><a href="{escape(url)}">{img}</a></td>'
            f'<td valign="middle" style="border-bottom:1px solid {LINE};padding-bottom:10px;word-wrap:break-word;overflow-wrap:anywhere;">'
            f'<a href="{escape(url)}" style="font-size:15px;font-weight:600;color:{INK};text-decoration:none;">{escape(title)}</a>{tag}'
            f'<div style="font-size:13px;color:{MUTED};margin-top:2px;">{escape(line2)}</div>{extra}</td></tr></table></td></tr>')


def button(url, label):
    return (f'<tr><td style="padding:16px 24px 4px;" align="center"><a href="{escape(url)}" '
            f'style="display:inline-block;background:{GREEN};color:#fff;font-weight:700;font-size:15px;'
            f'padding:12px 26px;border-radius:24px;text-decoration:none;">{label}</a></td></tr>')


def genius_extra(g, about=False):
    g = g or {}
    out = ""
    if about and g.get("description"):
        out += f'<div style="font-size:13px;color:#444;margin-top:8px;line-height:1.45;">{escape(g["description"])}</div>'
    if g.get("facts"):
        out += (f'<div style="font-size:13px;color:#444;margin-top:8px;line-height:1.45;padding:8px 10px;'
                f'background:#fffbe6;border-left:3px solid #f5c518;">💡 {escape(g["facts"][0])}</div>')
    if g.get("url"):
        out += (f'<div style="margin-top:6px;"><a href="{escape(g["url"])}" style="font-size:12px;font-weight:600;'
                f'color:#8a6d00;text-decoration:none;">Lire sur Genius →</a></div>')
    return out


def build_email(today, releases, playlist_url, podcasts, classics, songs, errors, best_of=None):
    week = today.isocalendar()[1]
    text = [f"🎶 Sorties de la semaine {week}", ""]
    html = [f'<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body style="margin:0;padding:0;background:{BG};font-family:{FONT};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center" style="padding:16px 8px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#fff;border-radius:14px;overflow:hidden;table-layout:fixed;">'
            f'<tr><td style="background:{INK};padding:26px 24px;"><div style="font-size:12px;letter-spacing:2px;color:{GREEN};font-weight:700;">SEMAINE {week}</div>'
            f'<div style="font-size:24px;font-weight:800;color:#fff;margin-top:4px;">Au menu cette semaine 🍝</div>'
            f'<div style="font-size:13px;color:#b3b3b3;margin-top:4px;">{len(releases)} sortie{"s" if len(releases) > 1 else ""} de tes artistes</div></td></tr>']

    html.append(section("🎶 Nouvelles sorties"))
    if releases:
        text.append("-- Musique --")
        for r in releases:
            text.append(f"{r['credits']} - {r['title']} [{BADGES[r['kind']][0]}]")
            html.append(row(r["cover"], r["title"], r["credits"], r["url"], r["kind"]))
        if playlist_url:
            text += ["", f"🔗 Playlist de la semaine : {playlist_url}"]
            html.append(button(playlist_url, "🎧 Écouter la playlist de la semaine"))
    else:
        text.append("Pas de nouvelle sortie cette semaine.")
        html.append(f'<tr><td style="padding:4px 24px;color:{MUTED};font-size:14px;">Pas de nouvelle sortie cette semaine.</td></tr>')

    if best_of:
        label, top, url = best_of
        text += ["", f"-- Best of {label} --", *(f"{i}. {t['credits']} - {t['title']}" for i, t in enumerate(top[:10], 1))]
        html.append(section(f"🏆 Best of {label}", f"Les sorties du mois les plus écoutées ({len(top)} titres)"))
        for i, t in enumerate(top[:10], 1):
            html.append(row(t["cover"], f"{i}. {t['title']}", t["credits"], t["url"]))
        if url:
            text.append(f"🔗 {url}")
            html.append(button(url, f"🏆 Écouter le best of {label}"))

    if podcasts:
        text += ["", "-- Podcasts --", *(f"{p['show']} - {p['title']}" for p in podcasts)]
        html.append(section("🎧 Podcasts"))
        for p in podcasts:
            html.append(row(p["cover"], p["title"], p["show"], p["url"]))

    if classics:
        text += ["", "-- Les Classiques Hip-Hop de la semaine --"]
        html.append(section("📀 Classiques hip-hop", "3 albums du top 200 Rolling Stone à (ré)écouter"))
        for c in classics:
            text += [f"• {c['artist']} - {c['album']} ({c['year']})", f"  {c['url']}"]
            html.append(row(c["cover"], c["album"], f"{c['artist']} · {c['year']}", c["url"],
                            extra=genius_extra(c["genius_info"], about=True)))

    if songs:
        text += ["", "-- Les Sons du Siècle --"]
        html.append(section("🎵 Sons du siècle", "3 morceaux parmi les meilleurs du 21e siècle (Rolling Stone)"))
        for t in songs:
            text += [f"• {t['artist']} - {t['song']} ({t['year']})", f"  {t['url']}"]
            html.append(row(t["cover"], t["song"], f"{t['artist']} · {t['year']}", t["url"],
                            extra=genius_extra(t["genius_info"])))

    if errors:
        text += ["", "Erreurs rencontrées :", *errors]
        html.append(section("⚠️ Erreurs rencontrées"))
        html.append(f'<tr><td style="padding:4px 24px;font-size:12px;color:#b00020;">'
                    + "<br>".join(escape(e) for e in errors) + "</td></tr>")

    html.append(f'<tr><td style="padding:24px;font-size:11px;color:#aaa;text-align:center;">Spotify Weekly Release Bot</td></tr>'
                "</table></td></tr></table></body></html>")
    return f"🎶 Sorties de la Semaine - WK{week}", "\n".join(text), "".join(html)


def send_email(subject, text_body, html_body=None):
    msg = MIMEMultipart("alternative")
    msg["From"] = os.getenv("EMAIL_USER")
    msg["To"] = os.getenv("EMAIL_TO")
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASSWORD"))
        server.send_message(msg)
    print(f"✅ Email envoyé : {subject}")


def send_failure_alert(exc):
    """Prévenir par mail plutôt que de découvrir la panne des semaines plus tard."""
    if isinstance(exc, SpotifyTokenExpired):
        subject = "⚠️ Spotify bot : refresh token expiré"
        body = ("Le refresh token Spotify a expiré, le bot ne peut plus tourner.\n\n"
                "Pour le renouveler, en local dans le repo :\n"
                "    python generate_refresh_token.py\n"
                "puis colle le token affiché dans le secret GitHub SPOTIPY_REFRESH_TOKEN :\n"
                "https://github.com/justinmartin/spotify_weekly_release_bot/settings/secrets/actions\n")
    else:
        subject = "❌ Spotify bot : échec du run hebdo"
        body = "Le run hebdomadaire a échoué :\n\n" + "".join(traceback.format_exception(exc))
    try:
        send_email(subject, body)
    except Exception as mail_err:
        print(f"⚠️ Impossible d'envoyer l'alerte : {mail_err}")
