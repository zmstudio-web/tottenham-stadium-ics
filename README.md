# Tottenham Hotspur Stadium events → calendar

Builds an iCalendar (`.ics`) feed of everything that fills the stadium and
therefore triggers the Haringey **Event Day** parking zone, road closures and
crowding on the Victoria line / buses around N17:

- Spurs **home fixtures** (Premier League via the public premierleague.com API;
  cups / Europe too if you add a free football-data.org token)
- **Concerts and non-football events** (NFL, boxing, etc.) scraped from
  `tottenhamhotspurstadium.com`
- Anything you add by hand in [`manual_events.json`](manual_events.json)

## Run it locally

```bash
pip3 install certifi        # one-off, fixes macOS SSL
python3 scrape.py           # writes tottenham_stadium_events.ics
```

Then in Apple/Google/Outlook Calendar choose **File → Import** (one-off), or
host the file and **Subscribe** to it (auto-refreshing) — see below.

## Auto-update with GitHub Actions

[`.github/workflows/update.yml`](.github/workflows/update.yml)
runs the scraper **twice a day** (05:17 & 17:17 UTC — edit the `cron` line to
change that, e.g. `17 5 */3 * *` for every 3 days), commits the refreshed
`.ics`, and publishes it to GitHub Pages so calendar apps can subscribe.

Setup:

1. Push this folder to a new GitHub repo.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write.**
4. Run the workflow once from the **Actions** tab (or wait for the cron).
5. *(Optional)* add a repo secret `FOOTBALL_DATA_TOKEN` (free key from
   <https://www.football-data.org/client/register>) to include cup / European
   home games.

Your subscription URL will be:

```
https://<your-username>.github.io/<repo-name>/tottenham_stadium_events.ics
```

### Subscribe (auto-refreshing)

- **iPhone/iPad:** Settings → Calendar → Accounts → Add Account → Other → Add
  Subscribed Calendar → paste the URL.
- **macOS Calendar:** File → New Calendar Subscription → paste the URL →
  set "Auto-refresh" to Daily.
- **Google Calendar:** Other calendars → + → From URL → paste the URL.
  (Google refreshes external feeds every ~12–24h.)

## Notes / limitations

- Concert start times from the stadium site are approximate (the site exposes a
  nominal 18:00 slot); treat the **day** as the reliable part.
- Multi-night runs (e.g. JAY-Z 4 + 5 Sep) are expanded to one entry per night by
  reading the date text on the event page.
- Cup and European fixtures are often announced only a few weeks ahead; the
  football-data.org token keeps those current.
- The scraper only overwrites the `.ics` when it finds at least one event, so a
  temporary site outage won't wipe your feed.
- Restriction timings in each event's notes are indicative — check
  [Haringey event-day parking](https://www.haringey.gov.uk/parking-roads-and-travel/parking/parking-restrictions/event-day-parking)
  and TfL before relying on them.
