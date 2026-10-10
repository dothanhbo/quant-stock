"""Restore the two exact private study archives into a new empty directory."""
import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--archives-dir', type=Path)
parser.add_argument('--destination', type=Path, required=True)
args = parser.parse_args()
manifest = json.loads((ROOT / 'RESTORE_MANIFEST.json').read_text())
destination = args.destination.expanduser().resolve()
assert not destination.exists(), 'Restoration requires a new directory; never overwrite existing work'
archives = []
for study in manifest['studies']:
    path = (args.archives_dir / study['archive_name'] if args.archives_dir else Path(study['private_archive_path']))
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    assert digest.hexdigest() == study['archive_sha256'], 'Private archive identity mismatch'
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(study['files_sha256'])
        for name, expected in study['files_sha256'].items():
            relative = PurePosixPath(name)
            assert not relative.is_absolute() and '..' not in relative.parts
            assert sha256(archive.read(name)).hexdigest() == expected, name
    archives.append((study, path))
destination.mkdir(parents=True)
for study, path in archives:
    target = destination / study['directory_name']
    target.mkdir()
    with zipfile.ZipFile(path) as archive:
        archive.extractall(target)
    for name, expected in study['files_sha256'].items():
        assert sha256((target / name).read_bytes()).hexdigest() == expected
print(json.dumps({'status': 'RESTORED_VERIFIED', 'destination': str(destination),
                  'studies': [study['directory_name'] for study, _ in archives],
                  'research_rerun': False}))
