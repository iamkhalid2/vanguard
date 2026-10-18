"""Single-column, table-free LaTeX resume. Plain article class + standard packages only, so it
compiles with tectonic or pdflatex and parses cleanly in Workday/Greenhouse/Lever."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def esc(s: str) -> str:
    return "".join(_ESC.get(ch, ch) for ch in s or "")


def url(u: str) -> str:
    return rf"\href{{{u}}}{{{esc(u.replace('https://', ''))}}}"


PREAMBLE = r"""\documentclass[10.5pt,a4paper]{article}
\usepackage[margin=1.6cm]{geometry}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[hidelinks]{hyperref}
\usepackage{enumitem}
\usepackage{titlesec}
\pdfgentounicode=1
\setlist[itemize]{leftmargin=1.2em,itemsep=1pt,topsep=2pt}
\titleformat{\section}{\large\bfseries}{}{0pt}{}[\vspace{-6pt}\rule{\linewidth}{0.4pt}]
\titlespacing{\section}{0pt}{8pt}{4pt}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\begin{document}
"""


def render(candidate: dict, headline: str, summary: str, skills: list[tuple[str, list[str]]],
           projects: list[dict], bullets_by_project: dict[str, list[str]]) -> str:
    c = candidate
    out = [PREAMBLE,
           rf"{{\LARGE\bfseries {esc(c['name'])}}}\\[2pt]",
           rf"{esc(headline)}\\",
           rf"{esc(c['location'])} \textbar{{}} {esc(c['email'])} \textbar{{}} {url(c['website'])} \textbar{{}} "
           rf"{url('https://github.com/' + c['github'])} \textbar{{}} {url(c['linkedin'])}\\",
           rf"{esc(c['right_to_work'])}",
           r"\section{Summary}", esc(summary),
           r"\section{Technical Skills}", r"\begin{itemize}"]
    for label, items in skills:
        out.append(rf"\item \textbf{{{esc(label)}:}} {esc(', '.join(items))}")
    out += [r"\end{itemize}", r"\section{Projects}"]
    for p in projects:
        link = p.get("repo") or p.get("demo")
        link_tex = r" \textbar{} " + url(link) if link else ""
        out.append(rf"\textbf{{{esc(p['name'])}}}{link_tex} \hfill {esc(p['years'])}\\")
        out.append(rf"\textit{{{esc(', '.join(p['stack_shown']))}}}")
        out.append(r"\begin{itemize}")
        out += [rf"\item {esc(b)}" for b in bullets_by_project[p["id"]]]
        out.append(r"\end{itemize}")
    out.append(r"\section{Experience}")
    for e in c["experience"]:
        out.append(rf"\textbf{{{esc(e['title'])}}}, {esc(e['company'])}, {esc(e['location'])} \hfill {esc(e['years'])}")
        out.append(r"\begin{itemize}")
        out += [rf"\item {esc(b)}" for b in e["bullets"]]
        out.append(r"\end{itemize}")
    out.append(r"\section{Education}")
    for ed in c["education"]:
        out.append(rf"\textbf{{{esc(ed['degree'])}}}, {esc(ed['school'])} \hfill {esc(ed['years'])}")
    out.append(r"\end{document}")
    return "\n".join(out) + "\n"


def compile_pdf(tex: str, out_pdf: Path) -> Path | None:
    """Compile with tectonic (preferred, single binary) or pdflatex. Returns None if neither is installed."""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    src = out_pdf.with_suffix(".tex")
    src.write_text(tex)
    if shutil.which("tectonic"):
        cmd = ["tectonic", "--outdir", str(out_pdf.parent), str(src)]
    elif shutil.which("pdflatex"):
        cmd = ["pdflatex", "-interaction=nonstopmode", "-output-directory", str(out_pdf.parent), str(src)]
    else:
        return None
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    return out_pdf if out_pdf.exists() else None
