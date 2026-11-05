import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from loguru import logger

from src.classifier import Classifier
from src.config import Settings
from src.drafter import Drafter
from src.eval import GoldLabel, score
from src.gmail import GmailMailbox, build_service, check_inbox, seed
from src.mailbox import FakeMailbox, Mailbox
from src.pipeline import run_triage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="support-inbox-triage",
        description="Triage the Lumenfold support inbox. Creates drafts only; nothing is ever sent.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="triage the inbox and write a report")
    run.add_argument("--source", choices=["mock", "gmail"], default="mock")
    run.add_argument("--data", type=Path, default=Path("data/emails.json"))
    run.add_argument("--out", type=Path, default=Path("runs"))
    seeder = commands.add_parser(
        "seed",
        help="insert the mock emails into the demo Gmail inbox (nothing is sent)",
    )
    seeder.add_argument("--data", type=Path, default=Path("data/emails.json"))
    evaluator = commands.add_parser(
        "eval",
        help="triage the mock inbox with the real model and score it against the gold labels",
    )
    evaluator.add_argument("--data", type=Path, default=Path("data/emails.json"))
    evaluator.add_argument("--gold", type=Path, default=Path("data/gold.json"))
    evaluator.add_argument("--out", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command == "seed" or (args.command == "run" and args.source == "gmail"):
        if not settings.demo_inbox:
            logger.error("Set TRIAGE_DEMO_INBOX to the demo Gmail address first")
            return 2
        service = build_service(settings)
        if args.command == "seed":
            seed(service, FakeMailbox.from_json(args.data).emails, settings.demo_inbox)
            return 0
        try:
            check_inbox(service, settings.demo_inbox)
        except ValueError:
            logger.error("Signed-in Gmail account is not TRIAGE_DEMO_INBOX")
            return 2
        mailbox: Mailbox = GmailMailbox(service)
    else:
        mailbox = FakeMailbox.from_json(args.data)

    client = anthropic.Anthropic(max_retries=3, timeout=60.0)
    faq = settings.faq_path.read_text()
    out_dir = args.out / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report = run_triage(
        mailbox,
        Classifier(client, settings),
        Drafter(client, settings, faq),
        faq,
        settings,
        out_dir,
    )
    if args.command == "eval":
        gold = [GoldLabel.model_validate(g) for g in json.loads(args.gold.read_text())]
        result = score(gold, report.outcomes)
        (out_dir / "eval.json").write_text(result.model_dump_json(indent=2))
        logger.info(
            "Eval: accuracy {:.1%}, injection recall {:.0%}, violations {}, escalation recall {:.0%}, invoice fields {:.0%}, flag errors {}",
            result.accuracy,
            result.injection_recall,
            len(result.violations),
            result.escalation_recall,
            result.invoice_fields_accuracy,
            len(result.flag_errors),
        )
    logger.info("Report: {}", (out_dir / "report.html").resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
