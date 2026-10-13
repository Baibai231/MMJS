\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{crypto_report}[2026/10/12 Readable competition report layout]

% Adapted from the reference report's crypto.cls, using fonts shipped with
% TeX Live/Overleaf so the source stays portable.
\LoadClass[12pt,a4paper,UTF8,AutoFakeBold]{ctexart}
\RequirePackage[a4paper,top=3.2cm,bottom=3.0cm,left=3.15cm,right=3.0cm]{geometry}
\RequirePackage{fontspec}
\RequirePackage{xeCJK}
\RequirePackage{fancyhdr}
\RequirePackage{titlesec}
\RequirePackage{setspace}
\RequirePackage{caption}

% Pick the first CJK family that actually exists on this system, so the source
% stays portable between TeX Live/Overleaf (Fandol) and Windows (SimSun/SimHei).
\newcommand{\report@pickfont}[3]{%
  \IfFontExistsTF{#1}{#2}{#3}%
}

\setmainfont{TeX Gyre Termes}
\report@pickfont{FandolSong-Regular}{%
  \setCJKmainfont{FandolSong-Regular}[BoldFont=FandolHei-Regular]%
  \setCJKsansfont{FandolHei-Regular}%
  \setCJKmonofont{FandolHei-Regular}%
  \newCJKfontfamily\reporthei{FandolHei-Regular}%
  \newCJKfontfamily\reportkai[AutoFakeBold=2]{FandolKai-Regular}%
  \newCJKfontfamily\reportjapanese[AutoFakeBold=2]{FandolSong-Regular}%
}{%
  \report@pickfont{Noto Serif CJK SC}{%
    \setCJKmainfont{Noto Serif CJK SC}[BoldFont=Noto Sans CJK SC]%
    \setCJKsansfont{Noto Sans CJK SC}%
    \setCJKmonofont{Noto Sans CJK SC}%
    \newCJKfontfamily\reporthei{Noto Sans CJK SC}%
    \newCJKfontfamily\reportkai[AutoFakeBold=2]{Noto Serif CJK SC}%
    \newCJKfontfamily\reportjapanese[AutoFakeBold=2]{Noto Serif CJK SC}%
  }{%
    \setCJKmainfont{SimSun}[BoldFont=SimHei]%
    \setCJKsansfont{SimHei}%
    \setCJKmonofont{SimHei}%
    \newCJKfontfamily\reporthei{SimHei}%
    \newCJKfontfamily\reportkai[AutoFakeBold=2]{KaiTi}%
    \newCJKfontfamily\reportjapanese[AutoFakeBold=2]{SimSun}%
  }%
}

\setlength{\parindent}{2em}
\setlength{\parskip}{4bp plus 1bp minus 1bp}
\setlength{\headheight}{18pt}
\setlength{\headsep}{0.8cm}
% Four-size (14 bp) body with approximately 25 bp baseline spacing.
\renewcommand{\normalsize}{\fontsize{14bp}{16.8bp}\selectfont}
\renewcommand{\small}{\fontsize{12bp}{14.4bp}\selectfont}
\AtBeginDocument{\normalsize}
\setstretch{1.5}
\setlength{\footskip}{1.2cm}

\titleformat{\section}[block]{\reportkai\fontsize{18}{27}\selectfont\bfseries}{\thesection}{0.8em}{}
\titleformat{name=\section,numberless}[block]{\centering\reporthei\fontsize{17}{25}\selectfont\bfseries}{}{0pt}{}
\titleformat{\subsection}[block]{\reporthei\fontsize{15}{23}\selectfont\bfseries}{\thesubsection}{0.7em}{}
\titleformat{\subsubsection}[block]{\reporthei\fontsize{14}{22}\selectfont\bfseries}{\thesubsubsection}{0.7em}{}
\titleformat{\paragraph}[block]{\reporthei\fontsize{14}{22}\selectfont\bfseries}{（\theparagraph）}{0pt}{}
\titleformat{\subparagraph}[block]{\reporthei\fontsize{14}{22}\selectfont\bfseries}{}{0pt}{}
% Keep the normal two-character paragraph indent after every heading level.
\titlespacing{\section}{0pt}{1.1em}{0.9em}
\titlespacing{\subsection}{0pt}{1em}{0.65em}
\titlespacing{\subsubsection}{0pt}{0.9em}{0.5em}
\titlespacing{\paragraph}{0pt}{0.7em}{0.25em}
\titlespacing{\subparagraph}{0pt}{0.6em}{0.2em}

\pagestyle{fancy}
\fancyhf{}
\fancyhead[C]{\small 全国密码技术竞赛作品设计报告}
\fancyfoot[C]{\thepage}
\renewcommand{\headrulewidth}{0.5pt}

\captionsetup[figure]{font=small,labelfont=bf,labelsep=none,skip=9pt}
\setlength{\textfloatsep}{16bp plus 2bp minus 2bp}
\setlength{\intextsep}{14bp plus 2bp minus 2bp}
\renewcommand{\figurename}{图}
\counterwithin{figure}{section}
\renewcommand{\thefigure}{\arabic{section}-\arabic{figure}}

% Table captions belong to the table and share automatic section numbering.
\captionsetup[table]{font=small,labelfont=bf,labelsep=space,justification=centering,singlelinecheck=false,position=top,skip=9pt}
\counterwithin{table}{section}
\renewcommand{\thetable}{\arabic{section}-\arabic{table}}

% Dedicated abstract layout: independent of the 14 bp report body.
\newenvironment{reportabstract}{%
  \begingroup
  \setstretch{1}
  \fontsize{12bp}{18bp}\selectfont
  \setlength{\parindent}{2em}
  \setlength{\parskip}{6bp}
  \section*{摘\quad 要}%
}{%
  \par\clearpage
  \endgroup
}

% Readable three-level contents, with room for up to two pages.
\makeatletter
\newcommand{\reporttableofcontents}{%
  \begingroup
  \setstretch{1}%
  \fontsize{12bp}{18bp}\selectfont
  \setlength{\parskip}{0pt}%
  \renewcommand*{\l@section}[2]{%
    \addvspace{5bp}%
    {\bfseries\@dottedtocline{1}{0em}{4.8em}{##1}{##2}}}%
  \renewcommand*{\l@subsection}[2]{%
    \@dottedtocline{2}{1.5em}{2.7em}{##1}{##2}}%
  \renewcommand*{\l@subsubsection}[2]{%
    \@dottedtocline{3}{3.5em}{3.5em}{##1}{##2}}%
  \tableofcontents
  \endgroup
}
\makeatother
