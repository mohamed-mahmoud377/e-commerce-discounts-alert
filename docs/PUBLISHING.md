# Publishing: from a found deal to the public channel

The first release to real users (decided 4 Nov 2026). Telegram used to send every deal to about 12 approved
friends in private. Now people follow a **public channel**, and nothing reaches it until an admin approves it
in a **private review group**. Code: `src/deals/publish.py`. Tests: `tests/test_publish.py`.

```
crawl → the rules (unchanged) → alerts row → screenshot taken, kept on disk
      → REVIEW GROUP: a detailed English card with [✅ Approve] [❌ Reject] [👁 Preview]
            ├─ Approve → current price checked → PUBLIC CHANNEL: short Arabic post, screenshot, 🛒 button
            ├─ Reject  → a reason is chosen and stored
            └─ nobody decides for 6 hours → ⌛ the card closes
```

The switch is two config keys. With only one (or none) set, everything works as before: deals go to the
active subscribers in private.

```toml
[telegram]
review_chat_id = "-1001234567890"  # the admins' private group
public_chat_id = "@channel_name"   # the public channel; /start's link is made from it
```

## Setting it up

The bot can't create chats; an admin does, once:

1. **Review group.** Telegram → New Group → add `@Jerrys_deals_bot` → make it an admin. The bot sends the owner the
   group's id in private (`my_chat_member`). Telegram can upgrade a group to a supergroup later, which changes
   its id: the bot tells the owner that too, and `/id` in the group shows the current one.
2. **Public channel.** New Channel → Public, choose its `t.me/<username>` → Administrators → add the bot with
   *Post*, *Edit* and *Delete messages*. Use a channel, not a group: members can't post (no spam) and there is
   no member limit.
3. Put both in `/etc/deals/config.toml` and `systemctl restart deals-coordinator`.

Only the owner and the helper admins (made in `/subscribers`) can press the buttons, whoever else is in the
group. Existing subscribers get no message about the change: the owner tells them. Their rows stay;
they just stop getting deals. `/start` now answers with the channel's link and nobody asks to join.

## The card (English, for the admins)

Written when the alert is created and kept in `reviews.card`, so it shows what was true then: the score and
confidence, the title (linked), shop, group and seller; the warnings; price against the usual price or
the shop's claim; history (days tracked, changes, range, how often this cheap, lowest we saw); the crossed-out
price; the same product elsewhere; rating and stock; earlier alerts; the admin's note on a hand-sent alert.

One number a line and a blank line between sections (changed 4 Nov 2026: the admins could not pick the numbers out
of `Usual 6,000 EGP over 9 d → −28%, saves 1,702`). The text is stored when the alert is created, so cards
already waiting keep the old layout until they close:

```
🟡 REVIEW #1454 · price_drop · score 31 · medium confidence

Zolo Power Bank, 20,000mAh 22.5W ...
noon · Chargers & power banks · seller: Egyptian German

⚠️ marketplace seller · low rating 3.7

💰 Price: 1,078 EGP
Usual: 1,374 EGP (over 4 days)
Drop: −22%
Saves: 296 EGP
Just before: 1,285 EGP
Crossed out: 1,470 EGP (claims −27%)

📊 Tracked: 4 days
Changes: 3 in 90 days
Range: 1,285 – 1,374 EGP
This cheap: 0% of the time
Lowest we saw

🏪 Elsewhere
Other seller: 1,300 EGP

Rating: ★ 3.7 (29)
Signals: limited_deal

Past alerts: 0 · found 23:18 Cairo · public tone: good
```

Warnings: renewed or used (from the title), noon marketplace seller, possible pricing mistake, low confidence, inflated
crossed-out price, a round usual price that may be the shop's placeholder (10,000 or 100,000 EGP, see
[SHOP_RULES.md](SHOP_RULES.md)), low rating, few reviews.

Cards carry no screenshot; **Preview** posts the exact public post into the group under the card.

Cards go out at one per loop round (about 19 a minute, below Telegram's 20 a minute for a group), best score
first, so a burst like 31 Oct's 336 alerts takes about 18 minutes and the best deals don't wait behind it.

**Reject** asks for a reason: inflated reference, used or renewed, bad seller, discount too small, not interesting,
wrong data, price already gone, other. These are stored in `reviews.reason`: they are the data for
deciding later which deals can skip review.

**Who decided** is written on the card, and only the first press counts (the update is guarded by the status).

## When the price moved before the approval

At the press, the product's current state decides (`_moved`):

| Now | What happens |
|---|---|
| About the same (within 1%) | Posted with the screenshot |
| Lower | Posted at the lower price, without the screenshot (it shows the old price) |
| Higher, still below the usual price | Asks first: "📣 Post at X" or reject; posted at X, quieter (tone "good", no "lowest" or "mistake" claim), no screenshot |
| Back at or above the usual price | Only reject: no longer a discount, an old button can't force it either |
| Sold out | Only reject |

## The post (Arabic, for everyone)

Egyptian colloquial, short, one message: the screenshot with the text as its caption (1,024 characters at most;
the longest of a week's 1,131 real alerts is 340) and a URL button to the shop. No screenshot: text only.

Lines: hook · short title · price instead of the usual price, % · saving · what the discount rests on · warnings (renewed,
possible mistake) · note · shop · hashtags (group and shop).

**The hook rotates and follows the quality** (`HOOKS`; `_pool` picks the pool, the next unused hook is taken, so
the same hook never comes twice in a row and a preview shows what will really go out):

| Pool | When | Example |
|---|---|---|
| `error` | price_error | 🚨 شكلها غلطة في السعر! الحق قبل ما تتصلّح |
| `new` | new listing (the shop's claim) | 🆕 نزل جديد وعليه خصم 40%! |
| `manual` | sent by hand | 📣 عرض اخترناه لك |
| `low` | lowest we saw, on 14+ days of history | 📉 أقل سعر شفناه عليه! |
| `crazy` | score 70+, or a solid low at least 40% below usual | 😱 السعر ده مش طبيعي! |
| `strong` | score 50+ or a drop of 35%+ | 🔥 عرض جامد! |
| `good` | the rest | 👌 خصم حقيقي مش وهمي |

**Every hook has to be true of every deal that can reach its pool.** A new listing rests on the shop's claim, so
its pool and its line never mention our history. "Lowest we saw" needs 14+ days (`review.solid_low_days`): replaying
a week of real alerts on 4 Nov 2026, 54% counted as a new low because tracking was only 10 days old, which made a −20%
deal sound like a −60% one and put 29% in `crazy`. With the rule: 40 crazy, 543 strong, 548 good of 1,131 alerts by
tier. All thresholds are in `[review]`.

## The screenshot

Taken as before (`shots.py`, the same one page per alert) but nothing waits for it: the card goes at once, so review
adds no delay before an admin sees the deal. It is kept on disk, `data_dir/shots/<alert_id>.jpg`, because it has to
last until the approval and survive restarts; memory would only hold it 3 hours. A job may be taken up to 30
minutes late instead of 30 seconds. About 110 KB each × 160 a day × 2 days = 35 MB at most (`sweep_images`, hourly).
Once uploaded, Telegram's `file_id` is used.

An approved deal waits up to 60 seconds for a screenshot still being taken.

## Delivery

Same idea as `deliveries`: the bot only writes the decision, the loop (`step`, every 3 s in `AlertSender`) sends. A post that
fails is tried 5 times (30 s, 1, 2, 4 minutes, or what Telegram's `retry_after` says); then the status is `failed`, the
card says why and has **Try again**, and `make health` fails. A photo Telegram refuses (not passing trouble) never
costs the post.

`reviews.status`: `pending` → `approved` → `published` (or `failed`); `rejected`; `expired`; `skipped` (below `review.min_score`).

Hand-sent alerts from the web UI go through review as well, with their note.

**First real run, 4 Nov 2026** (alert 1425, a blender on Amazon, −51%): found 20:49:25 UTC, screenshot from a home worker at :35 (10 s, 168 KB), approved at :39, posted at :42 with photo, text and button: 17 seconds from finding to the public post, with an admin already watching.

## What this means for the principles

- **Notice fast:** detection and the card are as fast as before. Publishing now waits for a person: this is a real
  cost to flash sales until the best deals can skip review (below).
- **Real discounts:** better. A person looks at every deal, and the price is checked at the press.
- **Block risk:** no new kind of request. Screenshots that used to be dropped after 30 s may now still be taken
  (17 of 138 in the last week were late), well under one extra page an hour per shop.

## Volume

About 160 deals a day (1,129 in the 7 days before 4 Nov 2026; 68% score below 40; noon 50%, Jumia 26%). The owner chose to
review all of them to start: `review.min_score` is the floor if that gets too much.

## Next

- A Review page in the web UI.
- "The deal is over": edit the public post when a crawl sees the price go back up.
- Reject reasons by shop and kind, in the web UI.
- Deals that can skip review, learned from the decisions (e.g. a kind and score that was approved 95% of the time).
- Affiliate links.
