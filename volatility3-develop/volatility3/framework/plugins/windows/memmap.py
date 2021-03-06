# This file is Copyright 2020 Volatility Foundation and licensed under the Volatility Software License 1.0
# which is available at https://www.volatilityfoundation.org/license/vsl-v1.0
#
import contextlib
import logging
from typing import List

from volatility3.framework import exceptions, renderers, interfaces
from volatility3.framework.configuration import requirements
from volatility3.framework.renderers import format_hints
from volatility3.plugins.windows import pslist

vollog = logging.getLogger(__name__)


class Memmap(interfaces.plugins.PluginInterface):
    """Prints the memory map"""

    _required_framework_version = (1, 0, 0)

    @classmethod
    def get_requirements(cls) -> List[interfaces.configuration.RequirementInterface]:
        # Since we're calling the plugin, make sure we have the plugin's requirements
        return [
            requirements.TranslationLayerRequirement(name = 'primary',
                                                     description = 'Memory layer for the kernel',
                                                     architectures = ["Intel32", "Intel64"]),
            requirements.SymbolTableRequirement(name = "nt_symbols", description = "Windows kernel symbols"),
            requirements.PluginRequirement(name = 'pslist', plugin = pslist.PsList, version = (2, 0, 0)),
            requirements.BooleanRequirement(name = 'coalesce', description = 'Clump output where possible',
                                            default = False, optional = True),
            requirements.IntRequirement(name = 'pid',
                                        description = "Process ID to include (all other processes are excluded)",
                                        optional = True),
            requirements.BooleanRequirement(name = 'dump',
                                            description = "Extract listed memory segments",
                                            default = False,
                                            optional = True)
        ]

    @classmethod
    def coalesce(cls, mapping_generator):
        stashed_offset = stashed_mapped_offset = stashed_size = stashed_mapped_size = stashed_mapped_layer = None
        for offset, size, mapped_offset, mapped_size, map_layer in mapping_generator:
            if stashed_offset is None or (stashed_offset + stashed_size != offset) or (
                    stashed_mapped_offset + stashed_mapped_size != mapped_offset) or (stashed_map_layer != map_layer):
                # The block isn't contiguous
                if stashed_offset is not None:
                    yield stashed_offset, stashed_size, stashed_mapped_offset, stashed_mapped_size, stashed_map_layer
                # Update all the stashed values after output
                stashed_offset = offset
                stashed_mapped_offset = mapped_offset
                stashed_size = size
                stashed_mapped_size = mapped_size
                stashed_map_layer = map_layer
            else:
                # Part of an existing block
                stashed_size += size
                stashed_mapped_size += mapped_size
        # Yield whatever's left
        if stashed_offset is not None:
            yield stashed_offset, stashed_size, stashed_mapped_offset, stashed_mapped_size, stashed_map_layer

    def _generator(self, procs):
        for proc in procs:
            pid = "Unknown"

            try:
                pid = proc.UniqueProcessId
                proc_layer_name = proc.add_process_layer()
                proc_layer = self.context.layers[proc_layer_name]
            except exceptions.InvalidAddressException as excp:
                vollog.debug("Process {}: invalid address {} in layer {}".format(pid, excp.invalid_address,
                                                                                 excp.layer_name))
                continue

            if self.config['coalesce']:
                coalesce = self.coalesce
            else:
                coalesce = lambda x: x
            if self.config['dump']:
                file_handle = self.open(f"pid.{pid}.dmp")
            else:
                # Ensure the file isn't actually created if not needed
                file_handle = contextlib.ExitStack()
            with file_handle as file_data:
                file_offset = 0
                for mapval in coalesce(proc_layer.mapping(0x0, proc_layer.maximum_address, ignore_errors = True)):
                    offset, size, mapped_offset, mapped_size, maplayer = mapval

                    file_output = "Disabled"
                    if self.config['dump']:
                        try:
                            data = proc_layer.read(offset, size, pad = True)
                            file_data.write(data)
                            file_output = file_handle.preferred_filename
                        except exceptions.InvalidAddressException:
                            file_output = "Error outputting to file"
                            vollog.debug("Unable to write {}'s address {} to {}".format(
                                proc_layer_name, offset, file_handle.preferred_filename))

                    yield (0, (format_hints.Hex(offset), format_hints.Hex(mapped_offset),
                               format_hints.Hex(mapped_size),
                               format_hints.Hex(file_offset), file_output))

                    file_offset += mapped_size
                    offset += mapped_size

    def run(self):
        filter_func = pslist.PsList.create_pid_filter([self.config.get('pid', None)])

        return renderers.TreeGrid([("Virtual", format_hints.Hex), ("Physical", format_hints.Hex),
                                   ("Size", format_hints.Hex), ("Offset in File", format_hints.Hex),
                                   ("File output", str)],
                                  self._generator(
                                      pslist.PsList.list_processes(context = self.context,
                                                                   layer_name = self.config['primary'],
                                                                   symbol_table = self.config['nt_symbols'],
                                                                   filter_func = filter_func)))
