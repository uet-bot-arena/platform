# OTTv2 rules (English)

## Board and pieces

Standard OTTv2 uses a 9×9 board. Columns are a–i, rows are 1–9. In JSON, row 0 is rank 9 and row 8 is rank 1: a1 is {row: 8, col: 0}; i9 is {row: 0, col: 8}.

Red's base is a1; Blue's base is i9. Bases start empty. Red moves first. Each side starts with three Rocks, three Papers and three Scissors.

| Side | Squares in order | Piece types in the same order |
|---|---|---|
| Blue | b9 d9 f9 h9 a8 c8 e8 g8 i8 | Rock Paper Scissors Rock Paper Scissors Rock Paper Scissors |
| Red | a2 c2 e2 g2 i2 b1 d1 f1 h1 | Scissors Paper Rock Scissors Paper Rock Scissors Paper Rock |

## Moves and captures

Move exactly one of your pieces one square in any of eight directions. The destination must be on the board and either empty or occupied by an opponent that your piece can capture. You cannot enter a friendly square, capture an equal type, or move onto a type that defeats yours. There are no sliding moves.

| Attacker | Captures |
|---|---|
| Rock | Scissors |
| Scissors | Paper |
| Paper | Rock |

A capture removes the defender and puts the attacker on its square. Equal types block one another.

## Winning and drawing

Capture the enemy base, eliminate every enemy piece of any one type, or leave the opponent with no legal moves to win. Losing all your pieces always loses. For customized rules, evaluation priority is base capture → all pieces lost → one type eliminated → control target → no legal moves. Resignation and a valid timeout claim end the game separately.

A position with the same side to move repeated three times draws. Standard games also draw at 512 plies or the wall-clock game limit (20 minutes); the ply limit scales for larger configured boards. A platform failure produces no score and requires a fresh attempt.

Each bot has its own clock for the entire game, running only on its turn with no increment. Ladder and the default official ranking use **10 seconds per bot**. Other configured practice/internal competitions may use 60 or 300 seconds. Read the pinned competition configuration; legacy competitions keep their old per-turn clocks.

An opponent may claim timeout after the 3-second grace period; late actions are still rejected.

A bot startup error is a technical loss. If both bots fail at startup, both lose; infrastructure errors are retried and never counted as draws. The browser clock is display-only; the grader's monotonic clock decides deadlines.

## Bot checks and competition ranking

Checks use 24 states three times each: **72 independent samples, at most 500 ms per sample**. This budget is independent of the game clock. Passing is required for competition entry but does not prove your bot can finish a whole game within its clock.

Ladder begins at fixed even hours in Vietnam time. Each round freezes the latest passing commit available at its start. New pushes are checked immediately but enter the next round. If a round overlaps the next boundary, the next round waits for a later fixed boundary.

For more than 64 teams, official ranking starts with 20 general rounds, seeded from ladder Elo. Each pairing plays twice with swapped colors. Prefer opponents within ten ranks, widening minimally only when necessary to avoid repeats. General-stage K is 32 for the first 20 official games per team, then 16.

The top 64 then play double round robin, 126 games per team, with frozen Elo. With at most 64 entrants, skip the general stage. Win 1 point, draw ½, loss 0. The top 16 qualify; ties use head-to-head points among tied teams, total wins, then frozen general-stage Elo. An unresolved selection-boundary tie needs organizer attention.

The published competition configuration may use smaller values for demonstrations.

## Strategy and customized rules

Protect your last piece of each type. Coordinate types so a piece threatening one of yours can be captured in return. Threaten the enemy base to force defensive moves; do not focus only on total piece count.

Organizers may pin customized board sizes, bases, initial placement and win conditions. Experimental SDK v3 rules may add energy, regions, move templates and Shield. Standard GitHub submissions use SDK v2; read the competition's pinned rules and supplied legal actions rather than guessing from the standard board.
