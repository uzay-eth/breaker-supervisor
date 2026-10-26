"""Export v0.2 faces from the firmware renderer; all inputs are SIM.

Run from the checkout root with --output <faces-v02-dir>. No hardware is accessed.
SETUP is a fixed design illustration, not a pairing implementation.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from helpers import Rig
from breaker.canonical import digest
from breaker.core import HOLD_MS, APPROVED_MS, NOT_SENT_MS
from face_host import render
from png import save_face


def models():
    r = Rig()
    result = {'starting': r.s.face_model()}
    r.arm(); result['armed'] = r.s.face_model()
    ticket, raw = r.hold(184000); r.panel()
    result['held_1840'] = r.s.face_model()
    r.release(); r.press()
    assert [(e[3], e[4]) for e in r.confirms()] == [(str(ticket), digest(raw))]
    result['approved_1840'] = r.s.face_model()
    r.panel(); r.step(APPROVED_MS, heartbeat=True); r.panel()
    r.hold(1840000); r.panel(); result['held_18400'] = r.s.face_model()
    r.step(HOLD_MS, heartbeat=True); result['not_sent_18400'] = r.s.face_model()
    assert r.s.pending is None and len(r.confirms()) == 1
    r.panel(); r.step(NOT_SENT_MS, heartbeat=True)
    r.command('TRIP', 'mail.mass_delete'); result['tripped'] = r.s.face_model()
    r.inputs[:2] = [1, 0]; r.step(50); result['off'] = r.s.face_model()
    result['setup'] = dict(result['off'], mode='SETUP')  # Design-only; no UART verb.
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--preview', action='store_true', help='label uncommitted review output honestly')
    ap.add_argument('--snapshot', action='store_true', help='fingerprint source files; works without git')
    args = ap.parse_args()
    if args.snapshot:
        # Bind every input used to create the frames, including the fixture driver.
        sources = sorted((ROOT/'breaker').glob('*.py')) + [
            ROOT/'breaker/font.bin', ROOT/'face_host.py', ROOT/'png.py',
            ROOT/'tests/helpers.py', Path(__file__).resolve()]
        h = hashlib.sha256()
        for source in sources:
            h.update(str(source.relative_to(ROOT)).encode()+b'\0')
            h.update(hashlib.sha256(source.read_bytes()).digest())
        provenance = 'source SHA-256 ' + h.hexdigest()
    else:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--', '.'], cwd=ROOT, text=True)
        if dirty and not args.preview:
            ap.error('commit supervisor changes first, or use --preview for uncommitted review images')
        provenance = 'UNCOMMITTED PREVIEW based on ' + commit if dirty else commit
    args.output.mkdir(parents=True, exist_ok=True)
    faces = models()
    for name, model in faces.items():
        save_face(render(model), args.output/(name+'.png'))
    (args.output/('RENDER-PROVENANCE.md' if args.snapshot else 'README.md')).write_text(
        '# Supervisor face textures\n\nSource ' + ('snapshot' if args.snapshot else 'commit') + ': `' + provenance + '`.\n\n'
        'Generated from the shared firmware renderer. DEMO is a host-only overlay; '
        'hardware always shows OPEN BENCH. All inputs here are simulated. '
        'SETUP / Code 4817 is design-only; no pairing protocol exists.\n\n'
        'Run from the supervisor source directory:\n\n```sh\n'
        'python3 tools/render_faces_v02.py ' + ('--snapshot ' if args.snapshot else '')
        + '--output "' + str(args.output) + '"\n```\n\n'
        'Native PNGs are 296×128, 1-bit; @8x previews are 2368×1024 nearest-neighbor.\n\n'
        + '\n'.join('- `'+name+'.png` / `'+name+'@8x.png`' for name in faces) + '\n\n'
        'APPROVED follows one exact-hash local release. NOT SENT follows the 60 s expiry '
        'of the altered payout. These pictures do not prove downstream delivery, '
        'panel refresh time or physical link isolation.\n')
    print('Rendered %d faces / %d PNGs from %s' % (len(faces), 2*len(faces), provenance))


if __name__ == '__main__': main()
