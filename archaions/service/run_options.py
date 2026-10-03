from pydantic import BaseModel, ConfigDict, Field, model_validator

KITS = {
    'SQK-LSK114': 'DNA ligation · Kit 14',
    'SQK-LSK114-XL': 'DNA ligation XL · Kit 14',
    'SQK-NBD114-24': 'DNA native barcoding · 24 barcodes',
    'SQK-NBD114-96': 'DNA native barcoding · 96 barcodes',
    'SQK-RBK114-24': 'DNA rapid barcoding · 24 barcodes',
    'SQK-RBK114-96': 'DNA rapid barcoding · 96 barcodes',
    'SQK-RAD114': 'DNA rapid · Kit 14',
    'SQK-RNA004': 'Direct RNA · RNA004',
    'SQK-RNA004-XL': 'Direct RNA XL · RNA004',
}
BARCODE_KITS = {k for k in KITS if 'NBD' in k or 'RBK' in k}
DNA_MODS = {'6mA': '6mA · all contexts', '4mC_5mC': '4mC + 5mC · all contexts',
            '5mCG_5hmCG': '5mC + 5hmC · CG contexts', '5mC_5hmC': '5mC + 5hmC · all contexts'}
RNA_MODS = {
    'hac': {'inosine_m6A': 'Inosine + m6A · all contexts', 'm6A_DRACH': 'm6A · DRACH contexts',
            'm5C': 'm5C · all contexts', 'pseU': 'Pseudouridine · all contexts'},
    'sup': {'inosine_m6A_2OmeA': 'Inosine + m6A + 2′-O-methyl-A · all contexts',
            'm6A_DRACH': 'm6A · DRACH contexts', 'm5C_2OmeC': 'm5C + 2′-O-methyl-C · all contexts',
            'pseU_2OmeU': 'Pseudouridine + 2′-O-methyl-U · all contexts', '2OmeG': '2′-O-methyl-G · all contexts'},
}

class RunOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kit: str = 'SQK-LSK114'
    modifications: list[str] = Field(default_factory=list, max_length=4)
    demultiplex: bool = False
    barcode_both_ends: bool = False
    trim: bool = True
    qc: bool = True
    min_qscore: float = Field(default=0, ge=0, le=50, allow_inf_nan=False)
    min_length: int = Field(default=0, ge=0, le=10000000)

    @model_validator(mode='after')
    def validate_settings(self):
        if self.kit not in KITS:
            raise ValueError('Choose a supported sequencing kit.')
        if self.demultiplex and self.kit not in BARCODE_KITS:
            raise ValueError('Demultiplexing requires a barcoding kit.')
        if self.barcode_both_ends and (not self.demultiplex or 'NBD' not in self.kit):
            raise ValueError('Both-end matching requires native-barcode demultiplexing.')
        return self

    def validate_model(self, quality):
        if quality not in ('hac', 'sup'):
            raise ValueError('Choose HAC or SUP.')
        allowed = RNA_MODS[quality] if self.kit.startswith('SQK-RNA') else DNA_MODS
        bases = set()
        for mod in self.modifications:
            if mod not in allowed:
                raise ValueError(f'{mod} is not supported for this kit and basecalling mode.')
            base = ('A' if mod in ('6mA', 'm6A_DRACH') or mod.startswith('inosine') else
                    'T' if mod.startswith('pseU') else 'G' if mod == '2OmeG' else 'C')
            if base in bases:
                raise ValueError('Select only one modification model per canonical base.')
            bases.add(base)
        self.modifications.sort()
        return self

def validate_chemistry(info, options):
    if info.sequencing_kit.upper() != options.kit:
        raise ValueError(f'Selected kit {options.kit} does not match POD5 kit {info.sequencing_kit}.')
    rna = options.kit.startswith('SQK-RNA')
    cells = {'FLO-MIN004RA', 'FLO-PRO004RA'} if rna else {'FLO-MIN114', 'FLO-PRO114M', 'FLO-PRO114HD'}
    if info.flow_cell_product_code not in cells or info.sample_rate != (4000 if rna else 5000):
        raise ValueError('Unsupported flow cell or sample rate: use RNA004 at 4 kHz or R10.4.1 Kit 14 at 5 kHz.')

def basecaller_flags(options):
    flags = []
    if options.modifications:
        flags += ['--modified-bases', *options.modifications]
    if options.demultiplex:
        flags += ['--kit-name', options.kit]
        if options.barcode_both_ends:
            flags += ['--barcode-both-ends']
    if not options.trim:
        flags += ['--no-trim']
    return flags
