import hashlib,json,tempfile,unittest
from pathlib import Path
import pandas as pd
from scripts import research_evidence_archive as e

class EvidenceTests(unittest.TestCase):
    def make(self,root):
        data=root/'data';data.mkdir();man=root/'man';man.mkdir()
        path=data/'X.csv.gz';pd.DataFrame({'open_time':[0,900000,1800000]}).to_csv(path,index=False,compression='gzip')
        sha=e.digest(path)
        (man/'manifest_shard_00.json').write_text(json.dumps({'summary':{'shard_index':0},'symbols':[{'symbol':'X','file':path.name,'rows':3,'first':0,'last':1800000}]}))
        base=root/'baseline.json';base.write_text(json.dumps({'expected_csv_sha256':{'X':sha}}))
        return data,man,base,path
    def test_matches_full_catalogue_stats_and_prior_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);data,man,base,_=self.make(root)
            e.verify_source(data,man,base,root/'check.json')
            self.assertEqual(json.loads((root/'check.json').read_text())['status'],'VERIFIED')
    def test_incomplete_cache_is_rejected_and_failure_saved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);data,man,base,path=self.make(root);path.unlink()
            with self.assertRaisesRegex(ValueError,'catalogue mismatch'):
                e.verify_source(data,man,base,root/'check.json')
            self.assertEqual(json.loads((root/'check.json').read_text())['status'],'INCOMPLETE')
    def test_same_stats_but_changed_source_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);data,man,base,path=self.make(root)
            with path.open('ab') as f:f.write(b'x')
            # Replace the prior SHA while keeping a readable unchanged CSV source.
            path.unlink();pd.DataFrame({'open_time':[0,900000,1800000]}).to_csv(path,index=False,compression='gzip')
            base.write_text(json.dumps({'expected_csv_sha256':{'X':'0'*64}}))
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                e.verify_source(data,man,base,root/'check.json')
    def test_archive_keeps_failed_evidence_and_validates_exact_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source';source.mkdir()
            (source/'FAILED.log').write_text('AssertionError: preserve this failure\n')
            (source/'zero.csv').write_text('profit\n-3\n')
            e.archive(source,root/'out','failed-study')
            manifest=json.loads((root/'out/EVIDENCE_MANIFEST.json').read_text())
            self.assertEqual(len(manifest['files']),2)
            for r in manifest['files']:self.assertEqual(e.digest(root/'out'/r['file']),r['sha256'])

if __name__=='__main__':unittest.main()

