"""Print bounded ERA5 request JSON; this command never contacts CDS."""
import argparse
import json
from .planning import request_plan


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',required=True)
    parser.add_argument('--end-exclusive',required=True)
    parser.add_argument('--max-days',type=int,default=2)
    args=parser.parse_args(argv)
    print(json.dumps(request_plan(None,start=args.start,end_exclusive=args.end_exclusive,max_days=args.max_days),indent=2))
    return 0
