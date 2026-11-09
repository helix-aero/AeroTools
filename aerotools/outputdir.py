"""The `Outputs` folder of the suite: the folder next to the Metashape project where the tools write their files."""

import os

OUTPUTS = "Outputs"


def outputs_folder(psx_path):
    """Output folder: ``Outputs`` next to the .psx (not created here)."""
    return os.path.join(os.path.dirname(os.path.abspath(psx_path)), OUTPUTS)
