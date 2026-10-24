# Writing a Python / C / C++ bot

## 1. Account, team and repository

Register with your school email, verify the emailed code, set a password, and create or join a four-person team. If a code is missing, check Spam; it expires ten minutes after sending. Use the invite code on Profile & team to add teammates.

The captain creates a **private** repository from https://github.com/uet-bot-arena/ott-bot-starter using the exact ott2026-<team-code> name shown by the hub. Install the UET Bot Arena GitHub App for that repository only, invite your teammates as collaborators, and paste its link on the team page. Repository linking happens once.

Keep bot source directly inside bot/. Use exactly one main file: bot.cpp, bot.c or bot.py. Helpers must use the same language and sit in the same directory. No binaries, symlinks, submodules, Git LFS or nested directories. The hub receives only bot/ from the main branch; students cannot upload ZIP files through the website.

Push to main to trigger checks. The Submit bot page shows the GitHub SHA, build log and Pass/Fail result. The starter repository includes a local runner; follow its README to test against the same easy sample bot before pushing. No extra pip packages are needed by the local runner.

## 2. Languages and resources

C++17 uses g++ -std=c++17 -O2 and the supplied json.hpp. C11 uses gcc -std=c11 -O2, libm and the supplied JSON helper. Python uses the installed PyPy runtime; only standard-library modules and your own source are available. There is no dependency-download step.

The sandbox blocks network access and access to private host files. One CPU and one process/thread are allowed. Standard native memory budgets are 256 MiB for C/C++ and 1,024 MiB for Python. Use the competition's pinned profile for exact limits. Do not create subprocesses or background threads.

## 3. JSON Lines protocol

The process receives init, returns ready, receives observation, returns action, and repeats observation/action until stdin closes. Use the **exact protocol-v2 envelopes in the supplied SDK**; preserve request IDs and turn IDs. The repository includes a complete working bot, sample inputs and a runner, so you can replace only its move-selection function.

Each frame is one UTF-8 JSON object terminated by LF. Flush stdout after each reply. Return exactly one action per observation; do not send unsolicited ready frames, speculative actions or multiple replies. Stdout is protocol-only; write brief debug messages to stderr.

The maximum stdout frame is 65,536 bytes including LF. Total stderr is limited to 262,144 bytes per process. Duplicate JSON keys, NaN, Infinity, comments, trailing data and extra response fields are rejected. Exit on stdin EOF.

The grader suspends the bot between turns, so it cannot think during the opponent's turn. Read the current observation instead of retaining assumptions about the board. Use the supplied legal-move generator and simulator, and return one legal action before the hard deadline.

## 4. Time management and debugging

Read timing.budget_ms in every observation. Leave margin for serialization, flushing and scheduling. Ladder and default official games allow 10 seconds per bot for the **whole game**, not ten seconds on every move. The clock runs only during that bot's turn and has no increment. Practice may offer other configured clocks.

Each check runs 72 independent samples with an independent 500 ms limit per sample. It is separate from the game clock. Review median, P95, maximum time, build details and diagnostics on the check page. A passing check does not guarantee enough time for a whole game; play practice games too.

For a timeout, reduce search depth or use iterative deepening with a firm deadline. For protocol errors, remove stdout debug prints, preserve IDs and flush every reply. For illegal moves, use the current observation and legal-action generator. For startup/build errors, open the commit's build log and check file names, language and imports.

## 5. Ladder, deadline and results

Ladder starts at even Vietnam hours. A round uses each team's latest **passing** commit available at its start. Pushes during play are checked immediately and used from the next round. A commit still checking or failing does not replace a previously passing version.

At the submission deadline, the hub uses GitHub push history, falling back up to five pushes for a passing version. Self-reported commit dates are not authoritative. Pushes after the deadline cannot change the locked bot. Your team page shows the SHA used now and the prospective SHA for the next round.

Official ranking uses a general Elo stage for fields above 64 teams, followed by double round robin for the top group. Round-robin games score win 1, draw ½, loss 0 without updating Elo. The top 16 qualify under the published tiebreaks. See Rules on this page and the competition configuration for exact values.

Follow your games live or open replays. Practice and ladder retain each team's latest 50 replays; results remain. Official replays are retained. Source history belongs in your team's Git repository: check out a SHA and run locally to revisit an older version.

## 6. Limits, privacy and support

Up to 30 new source versions per hour; four running and four queued private jobs per team. A new version may replace a queued check for the same purpose; running checks continue. The hub caches three source versions, two compiled binaries and twenty detailed check reports per team according to their roles.

Do not share passwords, email verification codes, GitHub tokens or grader tokens. To report a problem, send organizers the team code, SHA, job/game link, time and reproduction steps. Bot logs and source are private to your team and organizers. English/Vietnamese switching translates the interface; names, announcements and raw technical diagnostics retain their original text.
