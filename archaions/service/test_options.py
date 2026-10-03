import unittest
from types import SimpleNamespace
from run_options import RunOptions,validate_chemistry,basecaller_flags
from test_public import server,TestClient

class OptionsTests(unittest.TestCase):
 def test_combinations(self):
    for mode in ['hac','sup']:
      for mod in ['4mC_5mC','5mCG_5hmCG','5mC_5hmC']:
        self.assertEqual(len(RunOptions(modifications=['6mA',mod]).validate_model(mode).modifications),2)
    for mods in [['5mCG_5hmCG','5mC_5hmC'],['6mA','6mA'],['m6A'],['--help']]:
      with self.assertRaises(ValueError):RunOptions(modifications=mods).validate_model('hac')
    with self.assertRaises(ValueError):RunOptions(kit='SQK-RNA004',modifications=['6mA']).validate_model('sup')
    with self.assertRaises(ValueError):RunOptions(demultiplex=True)
    with self.assertRaises(ValueError):RunOptions(min_qscore=float('nan'))
    with self.assertRaises(ValueError):RunOptions(min_length=-1)
    flags=basecaller_flags(RunOptions(kit='SQK-NBD114-96',demultiplex=True,barcode_both_ends=True,trim=False))
    self.assertEqual(flags,['--kit-name','SQK-NBD114-96','--barcode-both-ends','--no-trim'])
 def test_metadata(self):
    info=SimpleNamespace(sequencing_kit='SQK-RNA004',flow_cell_product_code='FLO-PRO004RA',sample_rate=4000)
    validate_chemistry(info,RunOptions(kit='SQK-RNA004'))
    with self.assertRaises(ValueError):validate_chemistry(info,RunOptions())
 def test_settings_identity_and_persistence(self):
    a=TestClient(server.app,cookies={'archaions_session':'options-test'},headers={'Origin':'https://archaions.com'})
    body=dict(run='options',filename='x.pod5',sha256='b'*64,size=4,model='hac')
    first=a.post('/uploads',json=body).json()
    second=a.post('/uploads',json=body|{'options':{'modifications':['6mA','4mC_5mC'],'min_qscore':10}}).json()
    self.assertNotEqual(first['id'],second['id'])
    repeat=a.post('/uploads',json=body|{'options':{'modifications':['4mC_5mC','6mA'],'min_qscore':10}}).json()
    self.assertEqual(second['id'],repeat['id'])
    self.assertEqual(a.get('/jobs/'+second['id']).json()['options']['min_qscore'],10)
    for patch in [{'modifications':['5mC_5hmC','4mC_5mC']},{'kit':'fake'},{'demultiplex':True}]:
      self.assertEqual(a.post('/uploads',json=body|{'options':patch}).status_code,422)


class OutputTests(unittest.TestCase):
 def test_filters_keep_modification_tags_and_count_failures(self):
    import tempfile,json,pysam
    from pathlib import Path
    from run_outputs import filter_and_summarize
    folder=Path(tempfile.mkdtemp())
    with pysam.AlignmentFile(str(folder/'calls.bam'),'wb',header={'HD':{'VN':'1.6'}}) as bam:
      for i,(q,length) in enumerate([(20,100),(5,100),(20,10)]):
        read=pysam.AlignedSegment();read.query_name=str(i);read.flag=4;read.query_sequence='C'*length;read.query_qualities=[q]*length
        read.set_tag('qs',float(q));read.set_tag('MM','C+m?,0;');read.set_tag('ML',[200]);bam.write(read)
    result=filter_and_summarize(folder,RunOptions(min_qscore=10,min_length=50))
    self.assertEqual((result['passed_reads'],result['filtered_reads']),(1,2))
    self.assertEqual(result['total_bases'],210);self.assertEqual(result['read_n50'],100)
    with pysam.AlignmentFile(str(folder/'calls.bam'),'rb',check_sq=False) as bam:
      reads=list(bam.fetch(until_eof=True));self.assertEqual(len(reads),1)
      self.assertEqual(reads[0].get_tag('MM'),'C+m?,0;');self.assertEqual(list(reads[0].get_tag('ML')),[200])
    self.assertEqual(json.loads((folder/'qc.json').read_text())['passed_reads'],1)
    result=filter_and_summarize(folder,RunOptions(min_qscore=50))
    self.assertEqual(result['passed_reads'],0)

if __name__=='__main__':unittest.main()
