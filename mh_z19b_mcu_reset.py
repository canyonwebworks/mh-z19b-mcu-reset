import time
from copy import copy
from mycodo.inputs.base_input import AbstractInput
from mycodo.inputs.sensorutils import is_device

# ----------------------------------------------------------------------
# Command constants
CMD_READ_CO2   = bytearray([0xFF, 0x01, 0x86, 0, 0, 0, 0, 0, 0x79])
CMD_ABC_OFF    = bytearray([0xFF, 0x01, 0x79, 0, 0, 0, 0, 0, 0x86])
CMD_ABC_ON     = bytearray([0xFF, 0x01, 0x79, 0xA0, 0, 0, 0, 0, 0xE6])
CMD_ZERO_POINT = bytearray([0xFF, 0x01, 0x87, 0, 0, 0, 0, 0, 0x78])
CMD_MCU_RESET  = bytearray([0xFF, 0x01, 0x8D, 0, 0, 0, 0, 0, 0x73])

RANGE_CMDS = {
    '1000':  bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x03, 0xE8, 0x7B]),
    '2000':  bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x07, 0xD0, 0x8F]),
    '3000':  bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x0B, 0xB8, 0xA3]),
    '5000':  bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x13, 0x88, 0xCB]),
    '10000': bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x27, 0x10, 0x2F]),
}
# ----------------------------------------------------------------------


class InputModule(AbstractInput):
    """Sensor support class that monitors the MH‑Z19's CO₂ concentration."""

    def __init__(self, input_dev, testing: bool = False):
        super().__init__(input_dev, testing=testing, name=__name__)
        self.ser = None
        self.measuring = False
        self.calibrating = False
        self.measure_range = None
        self.abc_enable = False

        if not testing:
            self.setup_custom_options(INPUT_INFORMATION["custom_options"], input_dev)
            self.initialize()

    # --------------------------------------------------------------
    def initialize(self) -> None:
        import serial

        if not is_device(self.input_dev.uart_location):
            self.logger.error(
                f'Could not open "{self.input_dev.uart_location}". '
                "Check the device location is correct."
            )
            return

        try:
            with serial.Serial(
                port=self.input_dev.uart_location,
                baudrate=self.input_dev.baud_rate,
                timeout=1,
                write_timeout=5,
            ) as ser:
                self.ser = ser
                (self.abcon() if self.abc_enable else self.abcoff())
                if self.measure_range:
                    self.set_measure_range(self.measure_range)
                time.sleep(0.02)
        except serial.SerialException:
            self.logger.exception("Opening serial")

    # --------------------------------------------------------------
    def _wait_until_idle(self) -> None:
        while self.measuring:
            time.sleep(0.02)

    # --------------------------------------------------------------
    def get_measurement(self):
        """Read CO₂ concentration (ppm)."""
        if not self.ser:
            self.logger.error(
                "Error 101: Device not set up. "
                "See https://kizniche.github.io/Mycodo/Error-Codes#error-101"
            )
            return

        self.return_dict = measurements_dict.copy()
        self._wait_until_idle()
        self.measuring = True

        try:
            self.ser.flushInput()
            self.ser.write(CMD_READ_CO2)
            time.sleep(0.01)
            resp = self.ser.read(9)

            if not resp:
                self.logger.debug("No response")
                return
            if len(resp) < 4:
                self.logger.debug(f"Too few values in response '{resp}'")
                return
            if resp[0] != 0xFF or resp[1] != 0x86:
                self.logger.error("Bad checksum")
                return

            co2 = (resp[2] << 8) + resp[3]
            self.value_set(0, co2)
        except Exception:          # pragma: no cover – unexpected errors
            self.logger.exception("get_measurement()")
        finally:
            self.measuring = False

        return self.return_dict

    # --------------------------------------------------------------
    def abcoff(self):
        self.ser.write(CMD_ABC_OFF)

    def abcon(self):
        self.ser.write(CMD_ABC_ON)

    def set_measure_range(self, measure_range: str) -> None:
        cmd = RANGE_CMDS.get(measure_range)
        if cmd:
            self.ser.write(cmd)
        else:
            self.logger.error(f"Unsupported range '{measure_range}'")

    # --------------------------------------------------------------
    def calibrate_span_point(self, args_dict):
        if "span_point_value_ppmv" not in args_dict:
            self.logger.error("Missing span_point_value_ppmv")
            return
        if not isinstance(args_dict["span_point_value_ppmv"], int):
            self.logger.error(
                f"span_point_value_ppmv must be int, got {type(...)}"
            )
            return

        self._wait_until_idle()
        self.calibrating = True
        try:
            ppm = args_dict["span_point_value_ppmv"]
            self.logger.info(f"Span point calibration @ {ppm} ppm")
            b3, b4 = divmod(ppm, 256)
            chk = self.checksum([0x01, 0x88, b3, b4])
            self.ser.write(bytearray([0xFF, 0x01, 0x88, b3, b4, 0, 0x0B, 0xB8, chk]))
            time.sleep(0.02)
        finally:
            self.calibrating = False

    def calibrate_zero_point(self, args_dict):
        self._wait_until_idle()
        self.calibrating = True
        try:
            self.logger.info("Zero point calibration")
            self.ser.write(CMD_ZERO_POINT)
            time.sleep(0.02)
        finally:
            self.calibrating = False

    def mcu_reset(self, args_dict):
        self._wait_until_idle()
        try:
            self.logger.info("MCU reset")
            self.ser.write(CMD_MCU_RESET)
            time.sleep(0.02)
        except Exception:
            self.logger.exception("MCU reset failed")

    @staticmethod
    def checksum(array: list[int]) -> int:
        """Calculate the checksum used by the MH‑Z19 protocol."""
        return 0xFF - (sum(array) % 0x100) + 1