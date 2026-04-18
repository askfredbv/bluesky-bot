# Pioneer dimension — 40 candidate entries (draft)

**Purpose**: a starter list for you to cull. Mark each as ✅ keep, ❌ skip, or ❓ verify. Target final count: 20–30 keepers across both pools. The "huh, I didn't know that / I'd forgotten that" bar is the only filter — if you read an entry and shrug, it's out.

**Format**: each entry shows the proposed config shape (`category` is `pioneer` / `artifact` / `project`). Dated entries have `month/day/year`; undated have an `id` only.

---

## Dated entries (6)

These fire on their anniversary. Frame: *"On this day in YYYY, …"*

### 1. ✅ — UTF-8 sketched on a diner placemat
```python
{"category": "pioneer", "month": 9, "day": 2, "year": 1992,
 "title": "UTF-8 designed on a New Jersey diner placemat",
 "detail": "Ken Thompson and Rob Pike sketched the encoding over dinner. The whole world's text is now built on what they wrote on a napkin."}
```

### 2. ✅ — First registered domain name
```python
{"category": "artifact", "month": 3, "day": 15, "year": 1985,
 "title": "First .com domain ever registered",
 "detail": "symbolics.com. A Lisp Machine company in Cambridge, Mass. The domain still resolves — bought by an investor who keeps it as a museum."}
```

### 3. ✅ — First banner ad
```python
{"category": "artifact", "month": 10, "day": 27, "year": 1994,
 "title": "First banner ad in history",
 "detail": "AT&T on HotWired: 'Have you ever clicked your mouse right HERE?' Click-through rate: 44%. The web has been chasing that number ever since."}
```

### 4. ✅ — Carmack open-sources Quake
```python
{"category": "project", "month": 12, "day": 21, "year": 1999,
 "title": "Carmack open-sourced the Quake engine",
 "detail": "Three years after release, the full source went up on id's FTP. Spawned a generation of engines, mods, and the entire competitive shooter scene.",
 "link": "https://github.com/id-Software/Quake"}
```

### 5. ❌ — RFC 1149 (IP over avian carriers) implemented
```python
{"category": "project", "month": 4, "day": 28, "year": 2001,
 "title": "Bergen Linux User Group implemented RFC 1149",
 "detail": "Real packets, real pigeons, ~5km transmission. 9 packets sent, 4 returned. Latency measured in minutes. The RFC was an April Fool from 1990 — they took it literally.",
 "link": "https://www.blug.linux.no/rfc1149/"}
```

### 6. ❌ — Linux first announcement
```python
{"category": "pioneer", "month": 8, "day": 25, "year": 1991,
 "title": "Linux first announced on comp.os.minix",
 "detail": "Linus: 'just a hobby, won't be big and professional like gnu.' He'd been working on it for five months. The post is still on Google Groups."}
```

---

## Undated — Pioneer (14)

### 7. ✅/❌
```python
{"id": "sparck-jones-idf", "category": "pioneer",
 "title": "Karen Spärck Jones invented IDF in 1972",
 "detail": "Inverse Document Frequency. Every search engine on Earth still uses her formula. She was largely uncredited until the 2000s and resisted being called a pioneer."}
```

### 8. ✅/❌
```python
{"id": "grep-etymology", "category": "pioneer",
 "title": "grep is named after an ed command",
 "detail": "g/re/p — global, regex, print. Ken Thompson extracted that one ed command into a standalone tool overnight after Doug McIlroy asked for it."}
```

### 9. ✅/❌
```python
{"id": "perlman-spanning-tree", "category": "pioneer",
 "title": "Radia Perlman wrote spanning tree as a poem",
 "detail": "The original 1985 spec includes 'Algorhyme' — eight stanzas explaining the algorithm in verse. Every ethernet network on the planet still runs her protocol."}
```

### 10. ❌
```python
{"id": "ping-one-evening", "category": "pioneer",
 "title": "ping was written in one evening, 1983",
 "detail": "Mike Muuss named it after sonar. He wrote it after a hallway conversation about an IP stack bug. Forty years later, every connectivity check still bounces off his code."}
```

### 11. ✅
```python
{"id": "alan-kay-messaging", "category": "pioneer",
 "title": "Alan Kay regretted the term 'object-oriented'",
 "detail": "He coined it but later said he meant messaging, not classes. 'I'm sorry I long ago coined the term objects,' he wrote, 'because it gets people to focus on the lesser idea.'"}
```

### 12. ✅
```python
{"id": "liskov-adt", "category": "pioneer",
 "title": "Barbara Liskov invented abstract data types",
 "detail": "Most devs know LSP — the substitution principle. Few know she also designed CLU in 1974, the language that introduced ADTs, iterators, and exception handling. Almost everything modern OO inherits from CLU."}
```

### 13. ❌
```python
{"id": "knuth-bug-bounty", "category": "pioneer",
 "title": "Knuth pays $2.56 for bugs in his books",
 "detail": "Hexadecimal $1.00 — 'one hexadecimal dollar.' He hand-writes the cheques. Most recipients frame them rather than cash them. Knuth stopped sending real cheques in 2008 (fraud), now issues 'certificates of deposit' at the Bank of San Serriffe."}
```

### 14. ❌
```python
{"id": "vint-cerf-suits", "category": "pioneer",
 "title": "Vint Cerf wore three-piece suits to early IETF",
 "detail": "Deliberately. He wanted the IETF taken seriously by telcos and governments who assumed internet researchers were unwashed academics. The suit was the protocol."}
```

### 15. ✅
```python
{"id": "computer-was-a-job", "category": "pioneer",
 "title": "'Computer' used to be a job title",
 "detail": "Mostly held by women. They computed ballistic tables, astronomical positions, census results — by hand. The machines took the name from the people they replaced."}
```

### 16. ✅
```python
{"id": "eniac-six-women", "category": "pioneer",
 "title": "ENIAC was programmed by six uncredited women",
 "detail": "Kathleen Antonelli, Jean Bartik, Frances Spence, Marlyn Meltzer, Ruth Teitelbaum, Frances Holberton. They were called 'subnotables' in the 1946 press photos. Their names weren't restored until the 1980s."}
```

### 17. ✅
```python
{"id": "agc-rope-memory", "category": "pioneer",
 "title": "Apollo's memory was hand-woven by women",
 "detail": "Core rope memory. Each bit was a wire physically threaded through (or around) a tiny ferrite core. Raytheon called the workers LOL ROM — Little Old Lady ROM. One mistake meant rewinding the entire program."}
```

### 18. ✅
```python
{"id": "guido-monty-python", "category": "pioneer",
 "title": "Python is named after Monty Python",
 "detail": "Not the snake. Guido was reading Monty Python scripts the week he started. The community kept the in-jokes — 'spam', 'eggs', 'parrot', 'shrubbery' all show up in tutorials."}
```

### 19. ❌
```python
{"id": "torvalds-freax", "category": "pioneer",
 "title": "Linux was almost named Freax",
 "detail": "Linus thought naming it after himself was too egotistical. The FTP admin who hosted the early uploads disagreed and renamed the directory to 'linux'. Linus never changed it back."}
```

### 20. ✅
```python
{"id": "dijkstra-handwritten", "category": "pioneer",
 "title": "Dijkstra wrote 1300+ papers by hand",
 "detail": "Numbered EWD1 through EWD1318. He distributed them by photocopy, never used email or word processors. The full archive is online at UT Austin."}
```

### 21. ✅
```python
{"id": "zimmermann-pgp-book", "category": "pioneer",
 "title": "PGP source code was published as a book",
 "detail": "Phil Zimmermann printed the full source in a hardback so it could be exported under First Amendment protection. US crypto export laws applied to software, not books. MIT Press published it."}
```

---

## Undated — Forgotten artifacts (10)

### 22. ✅
```python
{"id": "trojan-coffee-pot", "category": "artifact",
 "title": "First webcam was a coffee pot",
 "detail": "Cambridge Computer Lab, 1991. Pointed at the Trojan Room coffee pot so researchers wouldn't trek to an empty one. Ran for 10 years. The pot is in a German museum."}
```

### 23. ✅
```python
{"id": "mother-of-all-demos", "category": "artifact",
 "title": "1968 Mother of All Demos",
 "detail": "Doug Engelbart, in 90 minutes, demoed the mouse, hypertext, video conferencing, real-time collaborative editing, and dynamic file linking. Every modern UI is downstream of this one demo."}
```

### 24. ❌
```python
{"id": "mouse-was-wood", "category": "artifact",
 "title": "First mouse was a wooden block",
 "detail": "Engelbart and Bill English, 1964. Hand-carved pine, two perpendicular wheels for X/Y. Officially called 'X-Y position indicator for a display system'. Patent filed; Engelbart got $10,000 royalty total."}
```

### 25. ✅
```python
{"id": "first-online-purchase", "category": "artifact",
 "title": "First online purchase was a Sting CD",
 "detail": "August 1994. NetMarket. Sting's Ten Summoner's Tales. The transaction used PGP for the credit card. The buyer just wanted to test that it worked."}
```

### 26. ✅
```python
{"id": "altavista-babelfish", "category": "artifact",
 "title": "AltaVista's Babelfish",
 "detail": "Launched 1997. Free machine translation between 13 languages, named after the Hitchhiker's Guide creature. Predated Google Translate by a decade. Yahoo killed it in 2012."}
```

### 27. ✅
```python
{"id": "vt100-still-here", "category": "artifact",
 "title": "Your terminal still speaks VT100",
 "detail": "Released 1978 by DEC. The escape sequences for cursor movement, colour, clearing the screen — all still in use. Every modern terminal emulator is a VT100 emulator with extras."}
```

### 28. ✅
```python
{"id": "yahoo-was-a-list", "category": "artifact",
 "title": "Yahoo started as a hand-curated list",
 "detail": "Two Stanford grad students, 1994, manually adding new sites to a hierarchy of folders. Originally called 'Jerry and David's Guide to the World Wide Web'. The directory survived until 2014."}
```

### 29. ❌
```python
{"id": "first-arpanet-message", "category": "artifact",
 "title": "First ARPANET message was 'LO'",
 "detail": "Charley Kline at UCLA tried to type LOGIN. The system crashed after the L and the O. The first message ever sent over the network was a typo and a half."}
```

### 30. ❌
```python
{"id": "hayes-at-commands", "category": "artifact",
 "title": "Your phone still uses Hayes AT commands",
 "detail": "Designed in 1981 for 300-baud modems. ATDT, ATH, +++. Modern smartphones still use AT commands to talk to their cellular modems. Forty-five years and counting."}
```

### 31. ✅
```python
{"id": "plan9-from-bell", "category": "artifact",
 "title": "Plan 9 from Bell Labs",
 "detail": "Unix's intended successor. Everything is a file — even network connections, even the window system. Beautiful, never caught on. Named after the Ed Wood film, which the authors loved unironically."}
```

---

## Undated — Wonderful weird projects (10)

### 32. ✅
```python
{"id": "paris-paper-plane", "category": "project",
 "title": "The Register flew a paper plane to space",
 "detail": "PARIS — Paper Aircraft Released Into Space. 2010, weather balloon, 27km altitude. Multi-part live build series. Plane recovered intact. Followed by LOHAN, a paper-plane-launched rocket.",
 "link": "https://www.theregister.com/Tag/Paris/Paper%20Aircraft%20Released%20Into%20Space/"}
```

### 33. ✅
```python
{"id": "story-of-mel", "category": "project",
 "title": "The Story of Mel",
 "detail": "1983 Usenet post by Ed Nather about a programmer who wrote self-modifying code on a drum-memory computer because timing the drum rotation was faster than using subroutines. The original real-programmer lore.",
 "link": "https://www.catb.org/jargon/html/story-of-mel.html"}
```

### 34. ❌
```python
{"id": "sqlite-public-domain", "category": "project",
 "title": "SQLite is in the public domain",
 "detail": "D. Richard Hipp deliberately released it without a license — he wanted no friction. Ships in every Android, iOS, browser, and aircraft. He runs the project from a small company in Charlotte, NC. Affidavits available on request for companies whose lawyers can't accept 'no license'."}
```

### 35. ❌
```python
{"id": "lowtech-solar-website", "category": "project",
 "title": "Low-Tech Magazine runs on solar",
 "detail": "Self-hosted on a small solar panel and battery. Goes offline when the weather is bad in Barcelona. Static dithered images, server runs on 1-2.5W. Online uptime is part of the design statement.",
 "link": "https://solar.lowtechmagazine.com/"}
```

### 36. ✅
```python
{"id": "curl-one-person", "category": "project",
 "title": "curl has had one maintainer for 25 years",
 "detail": "Daniel Stenberg started it in 1996. It runs in cars, satellites, every operating system. He still does most reviews himself. He keeps a list of every device he knows curl ships in — last count: more than 20 billion installs."}
```

### 37. ✅
```python
{"id": "stanford-bunny", "category": "project",
 "title": "The Stanford Bunny",
 "detail": "A scanned ceramic rabbit from 1994. Used as a benchmark for 3D rendering for 30+ years. If you've seen a teapot, a bunny, or a dragon in a graphics paper — they're the standard test models, dating back to actual physical objects on someone's desk."}
```

### 38. ✅
```python
{"id": "internet-toaster", "category": "project",
 "title": "First IoT device was a toaster",
 "detail": "John Romkey, 1990. Connected to the internet via TCP/IP, controlled with SNMP. Bread had to be loaded by hand. Demoed at INTEROP. The next year they added a robotic arm to load the bread."}
```

### 39. ❌
```python
{"id": "long-now-clock", "category": "project",
 "title": "The Clock of the Long Now",
 "detail": "A mechanical clock being built inside a Texas mountain to keep time for 10,000 years. Funded by Jeff Bezos. Ticks once a year. Cuckoos once a millennium. Designed by Danny Hillis."}
```

### 40. ❌
```python
{"id": "mickens-night-watch", "category": "project",
 "title": "James Mickens, 'The Night Watch'",
 "detail": "USENIX ;login: essay, 2014. The funniest piece of writing about systems programming. Opening line is about how systems programmers are the night watchmen of the digital world. Print it out.",
 "link": "https://www.usenix.org/system/files/1311_05-08_mickens.pdf"}
```

---

## Distribution

| Category | Count | Notes |
|---|---|---|
| Pioneer | 17 (3 dated, 14 undated) | People + name origins |
| Artifact | 11 (2 dated, 9 undated) | Things that existed, often surprisingly old |
| Project | 12 (2 dated, 10 undated) | Beautifully documented amateur cleverness |

Roughly 60% of entries cluster in the late-80s-to-mid-90s window — the era you said you followed live. That's deliberate; the bot's voice will sound most like yours when it shares stuff from your own enthusiast era. If you want broader coverage, swap in older or newer entries.

---

## Things I deliberately left out

- **Ada Lovelace, Hopper's moth, Apollo 11, Turing's Bombe**: too well-known. Textbook material.
- **Brendan Eich / 10 days of JavaScript**: blog-circuit fact, not in the spirit.
- **Hedy Lamarr / frequency hopping**: a regular on listicles.
- **Spam from Monty Python**: same.
- **Therac-25, Aaron Swartz**: the "huh" reaction collapses into "oh god, that's grim". Different content type, different voice rules.
- **Joel Spolsky / Steve Yegge / DHH essays**: dev culture, not history. Could be a separate dimension if you want one.
- **Anything from the last 10 years**: if it's still in someone's RSS reader, it's news, not history. Pioneer dimension starts at "things people forgot or never knew".

---

## How to give feedback

Two ways that work for me:

1. **Quick triage**: paste the list back with ✅ / ❌ / ❓ next to each number.
2. **Conversational**: tell me which categories feel right, which entries feel forced, and what's missing — I'll regenerate.

Once we've got a kept list of 20–30, the actual `src/config.py` block is mechanical: I'll convert your keepers into the real data structure and we can move to the implementation phase.

---

## Batch 2 — Forgotten heroes (14 candidates)

New `category: "hero"`. Bar: made a real dent in tech, most working devs couldn't name them. Same triage convention (✅ / ❌ / ❓).

### 41. ✅
```python
{"id": "bechtolsheim-google-cheque", "category": "hero",
 "title": "Bechtolsheim wrote a cheque to a company that didn't exist",
 "detail": "September 1998. $100,000 to 'Google Inc.' before incorporation. Page and Brin had to register the company to deposit it. Bechtolsheim had co-founded Sun a decade earlier."}
```

### 42. ✅
```python
{"id": "hejlsberg-four-languages", "category": "hero",
 "title": "Anders Hejlsberg shaped four eras of programming",
 "detail": "Wrote Turbo Pascal at 23. Then Delphi. Then C#. Then TypeScript. One person, four languages, four decades. Still ships code at Microsoft."}
```

### 43. ✅
```python
{"id": "moolenaar-vim-uganda", "category": "hero",
 "title": "Bram Moolenaar maintained vim alone for 30 years",
 "detail": "From 1991 until his death in 2023. Vim is 'charityware' — donations went to ICCF Uganda, supporting children orphaned by HIV. Half the world's developers used his editor; almost none knew about the orphans."}
```

### 44. ✅
```python
{"id": "bellard-output", "category": "hero",
 "title": "Fabrice Bellard's output is implausible",
 "detail": "Wrote QEMU. Wrote FFmpeg. Wrote TinyCC. Wrote a JavaScript engine. Computed pi to a then-record 2.7 trillion digits on a single desktop. Released LTE base station software. One person."}
```

### 45. ✅
```python
{"id": "gosling-not-just-java", "category": "hero",
 "title": "James Gosling wrote more than Java",
 "detail": "Also: the original Unix Emacs (Gosling Emacs, 1981), NeWS (a window system that lost to X11 but was technically superior), and the satellite data system at NASA Ames. Java is the smallest interesting thing on his CV."}
```

### 46. ❌
```python
{"id": "djb-vs-us-government", "category": "hero",
 "title": "Daniel J. Bernstein sued the US over crypto",
 "detail": "1995. Argued that source code is speech, protected by the First Amendment. Won. The ruling is why you can publish encryption code without a State Department licence. He also wrote qmail, djbdns, and Curve25519 — the elliptic curve in your phone's TLS."}
```

### 47. ✅
```python
{"id": "venema-postfix", "category": "hero",
 "title": "Most of the world's email goes through Wietse Venema's code",
 "detail": "Postfix. Written at IBM Research, released 1998. Designed because sendmail was a security nightmare. Quiet, secure, ubiquitous. Venema also wrote TCP Wrapper and SATAN, the first real network security scanner."}
```

### 48. ✅
```python
{"id": "allman-sendmail-student", "category": "hero",
 "title": "Eric Allman wrote sendmail as a student",
 "detail": "Berkeley, late 1970s. He needed to bridge ARPANET, UUCP, and the campus network. Sendmail handled most of the world's email for 25 years. He wasn't paid for it; he had a thesis to finish."}
```

### 49. ✅
```python
{"id": "wirth-law", "category": "hero",
 "title": "Niklaus Wirth and Wirth's Law",
 "detail": "Designed Pascal, Modula, Oberon — and the workstation that ran them. Wirth's Law: 'Software gets slower faster than hardware gets faster.' He observed it in 1995. It's only become more true."}
```

### 50. ✅
```python
{"id": "lynn-conway-vlsi", "category": "hero",
 "title": "Lynn Conway rewrote how chips are designed",
 "detail": "Co-authored the 1980 textbook that made VLSI design teachable. Every modern chip uses her structured methodology. Earlier in her career IBM fired her for transitioning; she rebuilt from scratch at Xerox PARC."}
```

### 51. ❌
```python
{"id": "goldberg-refused-jobs", "category": "hero",
 "title": "Adele Goldberg tried to refuse Steve Jobs",
 "detail": "1979, Xerox PARC. Jobs came to see Smalltalk. Goldberg was the lead and didn't want to demo — she knew he'd take everything. PARC management overruled her. She was right. The Macintosh shipped four years later."}
```

### 52. ✅
```python
{"id": "vixie-bind", "category": "hero",
 "title": "Paul Vixie ran most of the internet's DNS",
 "detail": "Wrote BIND — the DNS server that ~70% of authoritative name servers still use. Founded the first commercial anti-spam blocklist. Most of the internet's plumbing has his fingerprints on it."}
```

### 53. ✅
```python
{"id": "postel-rfcs", "category": "hero",
 "title": "Jon Postel was the RFC editor for 30 years",
 "detail": "Every internet protocol document from 1969 to 1998 went through him. He coined the robustness principle: 'Be conservative in what you do, be liberal in what you accept from others.' Quietly held the standards process together until his death at 55."}
```

### 54. ✅
```python
{"id": "theo-openssh", "category": "hero",
 "title": "You use Theo de Raadt's code every day",
 "detail": "OpenSSH — every server login, every git push over SSH, every CI pipeline pulling from a private repo. He runs OpenBSD with the same uncompromising rigour. Funded by an annual donation drive that keeps barely making it."}
```

---

## Batch 2 distribution

14 forgotten-hero candidates spanning four eras: '70s mainframe (Allman, Postel), '80s workstation/PC (Hejlsberg, Wirth, Conway, Goldberg, Bechtolsheim), '90s open-source plumbing (Venema, Vixie, Bernstein, Moolenaar, Bellard, de Raadt), '00s onward implicitly via long careers.

Quietly biased toward people whose work you've literally typed today (vim, ssh, postfix, dns, ffmpeg) — the "huh, *that* person made *that*?" reaction is stronger than abstract "look at this clever invention".

## Things I deliberately left out (heroes batch)

- **Linus Torvalds, DHH, Bjarne Stroustrup, Brian Kernighan**: too well-known
- **Aaron Swartz**: same grim-collapse problem as before
- **Jeff Dean**: famous-within-Google, but mostly a meme outside it
- **Brendan Gregg, Rich Hickey, John Ousterhout**: borderline; can add later if you want a second batch
- **Donald Becker (Beowulf), Alan Cox**: very niche even by this dimension's standards

