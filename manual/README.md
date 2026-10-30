# Manual install (script + package)

This is the original, integration-free setup: a Python script polled by a `command_line` sensor plus template sensors.
The HACS integration in the repository root is easier; use this only if you cannot or do not want a custom integration.
Do not run both at the same time.

Reads the **public share page** of a [Solaire Cloud](https://www.solairecloud.com) solar site and turns it into Home
Assistant sensors that work in the Energy dashboard. No official API exists for this; the script talks to the same
endpoint the share page uses, so it can break whenever Solaire changes their site.

> **Unofficial.** Not affiliated with or endorsed by Solaire. Use it only with a share link you were given for your own
> system, keep the polling interval gentle (60 s or more), and use it at your own risk. This repository does not
> contain the share page's encryption key; you supply it yourself (below).

## What you get

| Sensor | Unit | Notes |
|---|---|---|
| `sensor.solaire_pv_power` | W | live PV power |
| `sensor.solaire_pv_energy_today` / `_month` / `_year` / `_total` | kWh | `total_increasing`, usable as the Energy dashboard solar source (use `_total`) |
| `sensor.solaire_load_energy_today`, `_grid_import_today`, `_grid_export_today` | kWh | as reported by the site (may be 0 on grid-tied systems without a meter) |
| `sensor.solaire_self_use_today` | % | |
| `sensor.solaire_cloud_data` | timestamp | last successful poll; unavailable while a poll fails |

The script prints many more fields (5-minute power curve, best day, month projection, first/last light, a clear-sky
model, inverter-stop detection). They are exposed as attributes of `sensor.solaire_cloud_data`; add template sensors
for whatever you need.

Only PV-side data is available: no battery, no live house load, no live grid power.

## Install

1. Copy `solaire_fetch.py` to `/config/scripts/` and `packages/solaire.yaml` to `/config/packages/`
   (enable packages with `homeassistant: packages: !include_dir_named packages` if you have not already).
2. Add the values from `secrets.example.yaml` to your `secrets.yaml`:
   - `solaire_share_code`: the value after `shareCode=` in your share link.
   - `solaire_aes_key` and `solaire_aes_iv`: see the next section.
3. Restart Home Assistant (a new `command_line:` section needs a restart), then pick `sensor.solaire_pv_energy_total`
   as the solar source in **Settings, Dashboards, Energy**.

Test the script by hand: `python3 /config/scripts/solaire_fetch.py` prints one JSON object (it exits non-zero with a
short reason on failure; the share code is never printed). `--soft` always exits 0 and prints `{"ok": false}` instead,
which the package uses because Home Assistant logs the whole command line of a failing command.

## Getting the AES key and IV

The share page encrypts its requests with AES-128-CBC and a key and IV that are hard-coded in its own JavaScript.
They are not included here. To find them yourself:

1. Open your share link in a desktop browser, open the developer tools, and go to **Sources** (or **Network**, then
   open the main `assets/index-*.js` bundle).
2. Search the bundle for `CBC` or `AES.encrypt`. Next to it you will find two 16-character strings, used as the key
   and the IV.
3. Put them in `secrets.yaml` as `solaire_aes_key` and `solaire_aes_iv`.

## Protocol notes

- `POST https://www.solairecloud.com/mesh/web/v1/public/share/display/houseHold/screen/getUserOverview?data=<enc>`
- Plain headers: `shareCode`, `sharePageCode: 1000`, `crytoVer: 1`.
- The query `data` is AES(`timeStamp=<ms>&requestId=<uuid>`), the body is `{"data": AES(json)}` with
  `{"curDate","year","month","day"}`, and the response is `{"data": AES(json)}`. AES-128-CBC, PKCS7, base64.
- The response has `statisticsInfo`, `powerCurve.acPower` (288 five-minute slots, null = no report) and `energyList`
  (daily kWh for the month). Changing `curDate` returns past days.

## Notes

- After a failed request the script waits 1, 2, 4, 8 and then 10 minutes before trying again (state kept in
  `/tmp/solaire_backoff.json`).
- The clear-sky model assumes a 10 degree tilt facing south (`TILT_DEG`, `PLANE_AZ_DEG` in the script) and uses
  your Home Assistant latitude and longitude. Adjust it to your roof.
- Share links can expire (often yearly); when the sensor stays unavailable, check that the link still opens.

## Tests

`python3 -m unittest discover -s tests` (offline, uses a dummy key; needs `cryptography` or `pycryptodome`).

## License

MIT, see `LICENSE`.
