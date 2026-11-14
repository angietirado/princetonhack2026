"""Explicit operator capture of a frozen Nessie wallet; no dotenv or bank writes."""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
import os
import sys

from spending_api.lab_nessie import NessieReadClient, NessieReadError
from spending_api.lab_wallet import MAX_CENTS
from spending_api.lab_wallet_catalog import (
    PROFILE_ID,
    WalletCatalogError,
    check_wallet_profile_target,
    make_wallet_profile,
    write_wallet_profile,
)


class CaptureUsageError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's ordinary errors echo unknown argument values, potentially secrets.
        raise CaptureUsageError("Invalid capture arguments; use --help.") from None


def _parser():
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Explicit catalog output path; parent directory must exist.")
    parser.add_argument("--profile-id", required=True, help="Operator-defined lowercase slug; never a customer/account identifier.")
    parser.add_argument("--profile-name", required=True, help="Short neutral display name.")
    parser.add_argument("--description", default="Operator-captured Nessie snapshot, frozen for this simulation.")
    parser.add_argument("--mode", choices=("create", "append", "replace"), default="create",
                        help="create: new file; append: new profile ID; replace: existing profile ID.")
    parser.add_argument("--days", type=int, default=7, help="Projection window, 1 to 31 UTC calendar days.")
    parser.add_argument("--reserve-cents", type=int, default=0, help="Explicit nonnegative reserve in integer cents.")
    return parser


def _check_options(args):
    if not PROFILE_ID.fullmatch(args.profile_id):
        raise CaptureUsageError("Invalid capture options.")
    for value, maximum, required in ((args.profile_name, 120, True), (args.description, 600, False)):
        if (
            len(value) > maximum or value != value.strip() or (required and not value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise CaptureUsageError("Invalid capture options.")
    if not 1 <= args.days <= 31 or not 0 <= args.reserve_cents <= MAX_CENTS:
        raise CaptureUsageError("Invalid capture options.")


async def _capture(args, *, environ, reader_factory):
    api_key = environ.get("LAB_NESSIE_API_KEY")
    customer_id = environ.get("LAB_NESSIE_CUSTOMER_ID")
    if not isinstance(api_key, str) or not api_key.strip() or not isinstance(customer_id, str) or not customer_id.strip():
        raise CaptureUsageError("Explicit Nessie environment configuration is required.")
    check_wallet_profile_target(args.output, args.profile_id, mode=args.mode)
    reader = reader_factory(api_key=api_key, customer_id=customer_id)
    snapshot = await reader.capture()
    if not isinstance(snapshot, dict) or snapshot.get("source") != "nessie" or snapshot.get("customer_id") != customer_id:
        raise WalletCatalogError("invalid_capture_source")
    record = make_wallet_profile(
        profile_id=args.profile_id, profile_name=args.profile_name, description=args.description,
        snapshot=snapshot,
        projection_inputs={"days": args.days, "reserve_cents": args.reserve_cents, "expected_income": [], "events": []},
    )
    return write_wallet_profile(args.output, record, mode=args.mode)


_CATALOG_MESSAGES = {
    "catalog_locked": "Catalog is locked. Confirm no writer is active before an operator removes a stale lock.",
    "catalog_already_exists": "Create requires a new catalog. Choose append for a new profile or replace for an existing profile.",
    "catalog_missing": "Append and replace require an existing valid catalog.",
    "profile_already_exists": "Append requires a new profile ID. Replace must be selected explicitly to update an existing profile.",
    "profile_missing": "Replace requires an existing profile ID. Append must be selected explicitly to add one.",
    "output_directory_missing": "Create the output directory before capturing.",
    "invalid_capture_source": "Reader did not return a Nessie snapshot for the pinned customer.",
}


def main(argv=None, *, environ: Mapping[str, str] | None = None, reader_factory=None, stdout=None, stderr=None) -> int:
    """Inject only test doubles here; production always uses the fixed-origin reader."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    try:
        args = _parser().parse_args(argv)
        _check_options(args)
        context = asyncio.run(_capture(
            args, environ=os.environ if environ is None else environ,
            reader_factory=NessieReadClient if reader_factory is None else reader_factory,
        ))
    except CaptureUsageError:
        print("Capture configuration is invalid. Use --help and explicitly set LAB_NESSIE_API_KEY and LAB_NESSIE_CUSTOMER_ID in the process environment.", file=err)
        return 2
    except WalletCatalogError as error:
        print(_CATALOG_MESSAGES.get(error.code, "Catalog validation or write failed. Any existing catalog was preserved."), file=err)
        return 1
    except NessieReadError:
        print("Nessie capture failed. Any existing catalog was preserved. Check operator configuration and service availability.", file=err)
        return 1
    except KeyboardInterrupt:
        print("Capture interrupted. Any existing catalog was preserved.", file=err)
        return 130
    except Exception:
        # Never print provider exceptions: they may contain request URLs, headers or IDs.
        print("Capture failed. Any existing catalog was preserved. No bank writes were attempted.", file=err)
        return 1
    status = "ready" if context["projection"]["status"] == "ready" else "needs_review"
    print("Saved frozen Nessie wallet catalog. Projection status: " + status + ".", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
