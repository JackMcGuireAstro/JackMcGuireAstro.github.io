# Manual accessibility pass (not yet done)

Automated checks cannot replace this. axe-core (WCAG 2.x A/AA rules) runs in every browser
suite at 320–1280 px in light and dark mode; this checklist is for a person with a screen
reader. Record the date, device, browser and screen reader for each run, and file each
problem as a "Review finding" issue.

## Combinations
1. VoiceOver + Safari on macOS
2. VoiceOver + Safari on iOS (phone)
3. One other: NVDA + Firefox on Windows, or TalkBack + Chrome on Android

## Tasks (do each on CTAS and on WorldsIndex)
1. Land on the page. Is the page title and the first heading announced? Is it clear what the
   resource is within the first two headings?
2. Search for a record by name (CTAS: SN2026aabx; WorldsIndex: HD 209458 b). Is the number of
   matches announced? Can you open a match without the mouse?
3. Open the record. Is focus moved to the record's heading? Is the tab title updated?
4. Find the comparison of sources. Are the table's row and column headers announced? Is an
   inconsistent value announced as inconsistent (not by colour only)? Is a limit announced as
   a limit?
5. Open a plot. Is there a table and a text summary with units?
6. Export the comparison rows. Is the result announced?
7. Close the record. Does focus return to where you were?
8. Zoom to 200 % and 400 % (desktop): no content lost, no sideways scrolling of the page.
9. Turn on reduced motion: no animation that cannot be paused.
10. CTAS refreshes about every half hour: is new content announced without moving focus,
    and can automatic refresh be paused?

## Result table
| Date | Combination | Task | Pass / problem | Issue |
|---|---|---|---|---|
| | | | | |
