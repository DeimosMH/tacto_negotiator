"""Read-only local dashboard for competition JSON reports. No dotenv or service clients."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ASSETS = Path(__file__).with_name("web_ui")
MAX_REPORT_BYTES = 32 * 1024 * 1024
MATCH_FIELDS = ("variant_id", "scenario", "seed", "wave", "case_id", "deal", "valid", "supplier_id",
                "finished_before_cap", "score", "cost_eur", "reference_cost_eur", "regret_eur", "end_reason",
                "error", "invalid_moves", "thread_timeouts", "model_attempts", "fallbacks", "decision_wall_s",
                "supplier_delay_s", "virtual_elapsed_s", "elapsed_s", "session_id", "replay_url", "rounds",
                "opening_cost_eur", "alternative_cost_eur")
DECISION_FIELDS = ("route", "reason", "decision_wall_s", "budget_s", "model_attempts", "forced_close",
                   "elapsed_over_budget", "fallback", "deadline_override", "error", "submission_error",
                   "wave_seconds_left", "thread_seconds_left")
TERM_FIELDS = ("unit_price", "payment_days", "lead_time_days", "volume_per_year", "contract_months",
               "incoterm", "tooling_eur", "effective_date", "index_clause", "ppm_commitment",
               "chargeback_eur_per_complaint")
PRICE_FIELDS = ("supplier_id", "unit_price", "term_sheet_id", "status")


def select(data, fields):
    return {key: data[key] for key in fields if key in data} if isinstance(data, dict) else {}


def terms(data):
    result = select(data, TERM_FIELDS)
    if isinstance(result.get("index_clause"), dict):
        result["index_clause"] = select(result["index_clause"],
                                        ("series", "material_share", "deadband_pct", "base_month"))
    return result


def action(data):
    if not isinstance(data, dict):
        return None
    result = select(data, ("supplier_id", "action", "term_sheet_id"))
    result["term_sheets"] = [terms(sheet) for sheet in data.get("term_sheets", [])[:3]]
    return result


def public_report(data):
    """Serve only the report fields the dashboard uses; omit prompts, headers and provider ledgers."""
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        raise ValueError("Expected a competition report with a matches array")
    if len(data["matches"]) > 50_000:
        raise ValueError("Report exceeds 50,000 matches")
    result = select(data, ("source", "team_name", "phase", "status", "started_utc", "finished_utc", "limitations",
                           "excluded_from_comparison", "exclusion_reason", "policy_commit"))
    result["models"] = select(data.get("models"), ("fast", "strong"))
    result["config"] = select(data.get("config"), ("scoring_version", "seeds", "waves", "wave_seconds", "workers",
                                                   "holdout", "decision_seconds", "scenarios", "set",
                                                   "scoring_authority"))
    result["matches"] = []
    for match in data["matches"]:
        if not isinstance(match, dict) or not isinstance(match.get("decisions", []), list):
            raise ValueError("Invalid match record")
        row = select(match, MATCH_FIELDS)
        row["terms"] = terms(match.get("terms"))
        row["cost_breakdown"] = select(match.get("cost_breakdown"), (
            "price", "uncovered_need", "fallback_risk", "payment_terms", "freight", "late_delivery",
            "quality", "tooling", "index_clause", "index_risk", "relationship"))
        row["decisions"] = []
        for decision in match.get("decisions", []):
            step = select(decision, DECISION_FIELDS)
            for name in ("proposed_action", "sent_action"):
                step[name] = action(decision.get(name))
            step["standing_prices"] = [select(price, PRICE_FIELDS) for price in decision.get("standing_prices", [])]
            step["supplier_reply"] = select(decision.get("supplier_reply"), PRICE_FIELDS)
            row["decisions"].append(step)
        result["matches"].append(row)
    return result


def read_report(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds 32 MB")
    return public_report(json.loads(raw))


def create_server(report_dir, port=1234, host="127.0.0.1", allowed_hosts=()):
    """Bind loopback by default and expose three static assets plus explicitly selected JSON reports."""
    root = Path(report_dir).resolve()
    hostnames = {"localhost", "127.0.0.1", *(name.lower() for name in allowed_hosts)}
    if not root.is_dir():
        raise ValueError("Report directory does not exist; use --reports PATH")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, content, mime="application/json"):
            body = json.dumps(content, allow_nan=False).encode() if mime == "application/json" else content
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                             "frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                hostname = urlsplit("//" + self.headers.get("Host", "")).hostname
            except ValueError:
                hostname = None
            if hostname not in hostnames:
                self.reply(403, {"error": "Local host only"})
                return
            route = unquote(urlsplit(self.path).path)
            assets = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                      "/dashboard.js": ("dashboard.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
            if route in assets:
                name, mime = assets[route]
                self.reply(200, (ASSETS / name).read_bytes(), mime)
                return
            if route == "/api/reports":
                reports = []
                for path in sorted(root.glob("*.json")):
                    if path.is_symlink() or path.name.startswith(".") or not path.is_file():
                        continue
                    try:
                        data = read_report(path)
                        reports.append({"name": path.name, "matches": len(data["matches"]),
                                        "scoring_version": data["config"].get("scoring_version"),
                                        "excluded": bool(data.get("excluded_from_comparison"))})
                    except (OSError, ValueError, TypeError, AttributeError):
                        continue  # incomplete checkpoints and unrelated JSON are not selectable runs
                self.reply(200, reports)
                return
            if route.startswith("/api/reports/"):
                name = route.removeprefix("/api/reports/")
                path = root / name
                if name != Path(name).name or name.startswith(".") or path.suffix != ".json" or path.is_symlink():
                    self.reply(404, {"error": "Report not found"})
                    return
                try:
                    self.reply(200, read_report(path))
                except FileNotFoundError:
                    self.reply(404, {"error": "Report not found"})
                except (OSError, ValueError, TypeError, AttributeError):
                    self.reply(400, {"error": "Cannot read this competition report (limit: 32 MB)"})
                return
            self.reply(404, {"error": "Not found"})

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=Path("docs/results"),
                        help="Directory of competition JSON reports")
    parser.add_argument("--port", type=int, default=1234, help="Local port (default: 1234)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback; 0.0.0.0 in a container)")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="Also accept this Host name, e.g. the public name of a deployment (repeatable)")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.reports, args.port, args.host, args.allow_host)
    except (OSError, ValueError, OverflowError) as error:
        parser.error(str(error))
    print(f"Tacto dashboard: http://localhost:{server.server_port} — Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
