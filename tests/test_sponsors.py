import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vanguard import sponsors

CSV = '''"Organisation Name","Town/City","County","Type & Rating","Route"
"Stripe Payments UK Ltd","London","","Worker (A rating)","Skilled Worker"
"Monzo Bank Limited","London","","Worker (A rating)","Skilled Worker"
"Acme Seasonal Ltd","Leeds","","Temporary Worker (A rating)","Seasonal Worker"
'''

def test_register(tmp_path):
    p = tmp_path / "s.csv"; p.write_text(CSV)
    reg = sponsors.load(p)
    assert sponsors.is_skilled_worker_sponsor("Monzo Bank", reg) is True
    assert sponsors.is_skilled_worker_sponsor("Stripe Payments", reg) is True
    assert sponsors.is_skilled_worker_sponsor("Acme Seasonal", reg) is False
    assert sponsors.is_skilled_worker_sponsor("Unknown Co", reg) is None
