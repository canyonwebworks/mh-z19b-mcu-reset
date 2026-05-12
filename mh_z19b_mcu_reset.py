import time
from copy import copy
from mycodo.inputs.base_input import AbstractInput
from mycodo.inputs.sensorutils import is_device

# ----------------------------------------------------------------------
# Command constants
CMD_READ_CO2   = bytearray([0xFF, 0x01, 0x86, 0, 0, 0, 0, 0, 0x79])
CMD_ABC_OFF	   = bytearray([0xFF, 0x01, 0x79, 0, 0, 0, 0, 0, 0x86])
CMD_ABC_ON	   = bytearray([0xFF, 0x01, 0x79, 0xA0, 0, 0, 0, 0, 0xE6])
CMD_ZERO_POINT = bytearray([0xFF, 0x01, 0x87, 0, 0, 0, 0, 0, 0x78])
CMD_MCU_RESET  = bytearray([0xFF, 0x01, 0x8D, 0, 0, 0, 0, 0, 0x73])

RANGE_CMDS = {
	'1000':	 bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x03, 0xE8, 0x7B]),
	'2000':	 bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x07, 0xD0, 0x8F]),
	'3000':	 bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x0B, 0xB8, 0xA3]),
	'5000':	 bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x13, 0x88, 0xCB]),
	'10000': bytearray([0xFF, 0x01, 0x99, 0, 0, 0, 0x27, 0x10, 0x2F]),
}
# ----------------------------------------------------------------------


def constraints_pass_measure_range(mod_input, value):
	"""
	Check if the user input is acceptable
	:param mod_input: SQL object with user-saved Input options
	:param value: float
	:return: tuple: (bool, list of strings)
	"""
	errors = []
	all_passed = True
	# Ensure valid range is selected
	if value not in ['1000', '2000', '3000', '5000', '10000']:
		all_passed = False
		errors.append("Invalid range")
	return all_passed, errors, mod_input


# Measurements
measurements_dict = {
	0: {
		'measurement': 'co2',
		'unit': 'ppm'
	}
}

# Input information
INPUT_INFORMATION = {
	'input_name_unique': 'MH_Z19B_MCU_RESET',
	'input_manufacturer': 'Winsen',
	'input_name': 'MH-Z19B w/Reset',
	'input_library': 'serial',
	'measurements_name': 'CO2',
	'measurements_dict': measurements_dict,
	'url_manufacturer': 'https://www.winsen-sensor.com/sensors/co2-sensor/mh-z19b.html',
	'url_datasheet': 'https://www.winsen-sensor.com/d/files/MH-Z19B.pdf',

	'message': 'This is the B version of the sensor that includes the ability to conduct '
			   'automatic baseline correction (ABC).',

	'options_enabled': [
		'uart_location',
		'uart_baud_rate',
		'period',
		'pre_output'
	],
	'options_disabled': ['interface'],

	'interfaces': ['UART'],
	'uart_location': '/dev/ttyAMA0',
	'uart_baud_rate': 9600,

	'custom_options': [
		{
			'id': 'abc_enable',
			'type': 'bool',
			'default_value': False,
			'name': 'Automatic Baseline Correction',
			'phrase': 'Enable automatic baseline correction (ABC)'
		},
		{
			'id': 'measure_range',
			'type': 'select',
			'default_value': '5000',
			'options_select': [
				('1000', '0 - 1000 ppmv'),
				('2000', '0 - 2000 ppmv'),
				('3000', '0 - 3000 ppmv'),
				('5000', '0 - 5000 ppmv'),
				('10000', '0 - 10000 ppmv'),
			],
			'required': True,
			'constraints_pass': constraints_pass_measure_range,
			'name': 'Measurement Range',
			'phrase': 'Set the measuring range of the sensor'
		}
	],

	'custom_commands_message': 'Zero point calibration: activate the sensor in a 400 ppmv CO2 environment (outside '
							  'air), allow to run for 20 minutes, then press the Calibrate Zero Point button.<br>Span '
							  'point calibration: activate the sensor in an environment with a stable CO2 concentration'
							  ' between 1000 and 2000 ppmv (2000 recommended), allow to run for 20 minutes, enter the '
							  'ppmv value in the Span Point (ppmv) input field, then press the Calibrate Span Point '
							  'button. If running a span point calibration, run a zero point calibration first. A span '
							  'point calibration is not necessary and should only be performed if you know what you are'
							  ' doing and can accurately produce a 2000 ppmv environment.',
	'custom_commands': [
		{
			'id': 'calibrate_zero_point',
			'type': 'button',
			'name': 'Calibrate Zero Point'
		},
		{
			'id': 'span_point_value_ppmv',
			'type': 'integer',
			'default_value': 2000,
			'name': 'Span Point (ppmv)',
			'phrase': 'The ppmv concentration for a span point calibration'
		},
		{
			'id': 'calibrate_span_point',
			'type': 'button',
			'name': 'Calibrate Span Point'
		},
		{
			'id': 'mcu_reset',
			'type': 'button',
			'name': 'MCU Reset',
			'phrase': 'Click to reset your unit (if you have perhaps accidentally calibrated a span point)'
		}
	]
}

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
		except Exception:		   # pragma: no cover – unexpected errors
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