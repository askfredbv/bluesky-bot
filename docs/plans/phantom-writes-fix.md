# Plan — gate post-dependent state on actual delivery

> **Draft for review, not a commitment.** Written before implementation so the
> approach can be critiqued rather than the finished code. Delete this file when
> the fix lands.

## The defect (see BACKLOG §2)

`persistence_stage` writes three post-dependent states unconditionally, and the
Curator one uses the wrong item:

| State | Wrong how |
| :--- | :--- |
| `recent_topics` | uses `news_items[0]['detected_topic']`, but the Curator may have written about a different item (`chosen_link`, #51); also written when nothing shipped |
| `recent_mode_topics` | written when nothing shipped |
| `pioneer_recent` | written when nothing shipped — burns a multi-week cooldown on an unposted entry |

`main()` calls `persistence_stage` unconditionally; `AutomationPayload` drops the
`bsky_sent_uris` / `mastodon_sent_ids` that `BroadcastPayload` already carries.

## Proposed shape

**Resolve once, pass the answer — not the inputs.** `broadcasting_stage` already
resolves the chosen item (it realigns `link_meta` and `source_domain` to it).
Re-deriving that downstream duplicates the resolution and lets the two drift,
which is exactly how this bug arose: #51 updated one consumer of the choice and
not the other.

```
broadcasting_stage    already resolves the chosen item
                      -> ALSO resolve posted_topic_category
BroadcastPayload      + posted_topic_category
post_run_automation   delivered = bool(bsky_sent_uris or mastodon_sent_ids)
AutomationPayload     + delivered, + posted_topic_category
persistence_stage     gate ALL three cooldown writes on `delivered`
```

No new data is computed — `BroadcastPayload` already has the delivery lists.

## Tests

- non-top pick records the **chosen** item's topic, not `news_items[0]`'s
- a no-delivery run records nothing in any of the three states
- a normal delivery still records as before (regression guard)

## No migration

`recent_topics` is a rolling 5-slot list (washes out in ~5 Curator runs);
`pioneer_recent` is timestamped and prunes on cooldown expiry. Both self-heal.

## Open question — `seen_data["links"]`

Written on the same unconditional path, so a failed run permanently retires every
article it was offered. Gating it on delivery means a launch we failed to post
stays eligible tomorrow, instead of a broadcast failure silently burying the
story we were trying to publish. Inclined to gate it.

Separately (NOT in scope): it marks all ~5 offered candidates seen, not just the
posted one, burning four unposted stories per run. Possibly deliberate.
