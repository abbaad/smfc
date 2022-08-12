#!/usr/bin/python3
#
#   smfc.py (C) 2020-2022, Peter Sulyok
#   IPMI fan controller for Super Micro X9/X10/X11 motherboards.
#
import argparse
import configparser
import glob
import os
import platform
import subprocess
import sys
import syslog
import time
from typing import List, Callable


# Program version string
version_str: str = '2.1'


class Log:
    """Log class. This class can send log messages considering different log levels and different outputs"""

    # Configuration parameters.
    log_level: int                      # Log level
    log_output: int                     # Log output
    msg: Callable[[int, str], None]     # Function reference to the log function (based on log output)

    # Constants for log levels.
    LOG_NONE: int = 0
    LOG_ERROR: int = 1
    LOG_INFO: int = 2
    LOG_DEBUG: int = 3

    # Constants for log outputs.
    LOG_STDOUT: int = 0
    LOG_STDERR: int = 1
    LOG_SYSLOG: int = 2

    def __init__(self, log_level: int, log_output: int) -> None:
        """Initialize Log class with log output and log level.

        Args:
            log_level (int): user defined log level (LOG_NONE, LOG_ERROR, LOG_INFO, LOG_DEBUG)
            log_output (int): user defined log output (LOG_STDOUT, LOG_STDERR, LOG_SYSLOG)
        """
        # Setup log configuration.
        if log_level not in {self.LOG_NONE, self.LOG_ERROR, self.LOG_INFO, self.LOG_DEBUG}:
            raise ValueError(f'Invalid log level value ({log_level})')
        self.log_level = log_level
        if log_output not in {self.LOG_STDOUT, self.LOG_STDERR, self.LOG_SYSLOG}:
            raise ValueError(f'Invalid log output value ({log_output})')
        self.log_output = log_output
        if self.log_output == self.LOG_STDOUT:
            self.msg = self.msg_to_stdout
        elif self.log_output == self.LOG_STDERR:
            self.msg = self.msg_to_stderr
        else:
            self.msg = self.msg_to_syslog
            if 'Windows' not in platform.platform():
                syslog.openlog('smfc.service', facility=syslog.LOG_DAEMON)

        # Print the configuration out at DEBUG log level.
        if self.log_level >= self.LOG_DEBUG:
            self.msg(Log.LOG_DEBUG, 'Logging module was initialized with:')
            self.msg(Log.LOG_DEBUG, f'   log_level = {self.log_level}')
            self.msg(Log.LOG_DEBUG, f'   log_output = {self.log_output}')

    def map_to_syslog(self, level: int) -> int:
        """Map log level to syslog values.

            Args:
                level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            Returns:
                int: syslog log level
            """
        syslog_level = syslog.LOG_ERR
        if level == self.LOG_INFO:
            syslog_level = syslog.LOG_INFO
        elif level == self.LOG_DEBUG:
            syslog_level = syslog.LOG_DEBUG
        return syslog_level

    def level_to_str(self, level: int) -> str:
        """Convert a log level to a string.

            Args:
                level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            Returns:
                str: log level string
            """
        string = 'ERROR'
        if level == self.LOG_INFO:
            string = 'INFO'
        elif level == self.LOG_DEBUG:
            string = 'DEBUG'
        return string

    def msg_to_syslog(self, level: int, msg: str) -> None:
        """Print a log message to syslog.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            msg (str): log message
        """
        if level is not self.LOG_NONE:
            if level <= self.log_level:
                syslog.syslog(self.map_to_syslog(level), msg)

    def msg_to_stdout(self, level: int, msg: str) -> None:
        """Print a log message to stdout.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            msg (str):  log message
        """
        if level is not self.LOG_NONE:
            if level <= self.log_level:
                print(f'{self.level_to_str(level)}: {msg}', flush=True, file=sys.stdout)

    def msg_to_stderr(self, level: int, msg: str) -> None:
        """Print a log message to stderr.

        Args:
            level (int): log level (LOG_ERROR, LOG_INFO, LOG_DEBUG)
            msg (str):  log message
        """
        if level is not self.LOG_NONE:
            if level <= self.log_level:
                print(f'{self.level_to_str(level)}: {msg}', flush=True, file=sys.stderr)


class Ipmi:
    """IPMI interface class. It can set/get modes of IPMI fan zones and can set IPMI fan levels using ipmitool."""

    log: Log                            # Reference to a Log class instance
    command: str                        # Full path for ipmitool command.
    fan_mode_delay: float               # Delay time after execution of IPMI set fan mode function
    fan_level_delay: float              # Delay time after execution of IPMI set fan level function

    # Constant values for IPMI fan modes:
    STANDARD_MODE: int = 0
    FULL_MODE: int = 1
    OPTIMAL_MODE: int = 2
    HEAVY_IO_MODE: int = 4

    # Constant values for IPMI fan zones:
    CPU_ZONE: int = 0
    HD_ZONE: int = 1

    # Constant values for the results of IPMI operations:
    SUCCESS: int = 0
    ERROR: int = -1

    def __init__(self, log: Log, config: configparser.ConfigParser) -> None:
        """Initialize the Ipmi class with a log class and with a configuration class.

        Args:
            log (Log): Log class
            config (configparser.ConfigParser): configuration values
        """
        # Set default or read from configuration
        self.log = log
        self.command = config['Ipmi'].get('command', '/usr/bin/ipmitool')
        self.fan_mode_delay = config['Ipmi'].getint('fan_mode_delay', fallback=10)
        self.fan_level_delay = config['Ipmi'].getint('fan_level_delay', fallback=2)
        # Validate configuration
        # Check 1: a valid command can be executed successfully.
        try:
            subprocess.run([self.command, 'sdr'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as e:
            raise e
        # Check 2: fan_mode_delay must be positive.
        if self.fan_mode_delay < 0:
            raise ValueError(f'Negative fan_mode_delay ({self.fan_mode_delay})')
        # Check 3: fan_mode_delay must be positive.
        if self.fan_level_delay < 0:
            raise ValueError(f'Negative fan_level_delay ({self.fan_level_delay})')
        # Print the configuration out at DEBUG log level.
        if self.log.log_level >= self.log.LOG_DEBUG:
            self.log.msg(self.log.LOG_DEBUG, 'Ipmi module was initialized with :')
            self.log.msg(self.log.LOG_DEBUG, f'   command = {self.command}')
            self.log.msg(self.log.LOG_DEBUG, f'   fan_mode_delay = {self.fan_mode_delay}')
            self.log.msg(self.log.LOG_DEBUG, f'   fan_level_delay = {self.fan_level_delay}')

    def get_fan_mode(self) -> int:
        """Get the current IPMI fan mode.

        Returns:
            int: fan mode (ERROR, STANDARD_MODE, FULL_MODE, OPTIMAL_MODE, HEAVY_IO_MODE)
        """
        r: subprocess.CompletedProcess  # result of the executed process
        m: int                          # fan mode

        # Read the current IPMI fan mode.
        try:
            r = subprocess.run([self.command, 'raw', '0x30', '0x45', '0x00'],
                               check=False, capture_output=True, text=True)
            m = int(r.stdout)
        except (FileNotFoundError, ValueError) as e:
            raise e
        return m

    def get_fan_mode_name(self, mode: int) -> str:
        """Get the name of the specified IPMI fan mode.

        Args:
            mode (int): fan mode
        Returns:
            str: name of the fan mode ('ERROR', 'STANDARD MODE', 'FULL MODE', 'OPTIMAL MODE', 'HEAVY IO MODE')
        """
        fan_mode_name: str              # Name of the fan mode

        fan_mode_name = 'ERROR'
        if mode == self.STANDARD_MODE:
            fan_mode_name = 'STANDARD_MODE'
        elif mode == self.FULL_MODE:
            fan_mode_name = 'FULL_MODE'
        elif mode == self.OPTIMAL_MODE:
            fan_mode_name = 'OPTIMAL_MODE'
        elif mode == self.HEAVY_IO_MODE:
            fan_mode_name = 'HEAVY IO MODE'
        return fan_mode_name

    def set_fan_mode(self, mode: int) -> None:
        """Set the IPMI fan mode.

        Args:
            mode (int): fan mode (STANDARD_MODE, FULL_MODE, OPTIMAL_MODE, HEAVY_IO_MODE)
        """
        # Validate mode parameter.
        if mode not in {self.STANDARD_MODE, self.FULL_MODE, self.OPTIMAL_MODE, self.HEAVY_IO_MODE}:
            raise ValueError(f'Invalid fan mode value ({mode}).')
        # Call ipmitool command and set the new IPMI fan mode.
        try:
            subprocess.run([self.command, 'raw', '0x30', '0x45', '0x01', str(mode)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as e:
            raise e
        # Give time for IPMI system/fans to apply changes in the new fan mode.
        time.sleep(self.fan_mode_delay)

    def set_fan_level(self, zone: int, level: int) -> None:
        """Set the IPMI fan level in a specific zone. Raise an exception in case of invalid parameters.

        Args:
            zone (int): fan zone (CPU_ZONE_TAG, HD_ZONE_TAG)
            level (int): fan level in % (0-100)
        """
        # Validate zone parameter
        if zone not in {self.CPU_ZONE, self.HD_ZONE}:
            raise ValueError(f'Invalid value: zone ({zone}).')
        # Validate level parameter (must be in the interval [0..100%])
        if level not in range(0, 101):
            raise ValueError(f'Invalid value: level ({level}).')
        # Set the new IPMI fan level in the specific zone
        try:
            subprocess.run([self.command, 'raw', '0x30', '0x70', '0x66', '0x01', str(zone), str(level)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as e:
            raise e
        # Give time for IPMI and fans to spin up/down.
        time.sleep(self.fan_level_delay)


class FanController:
    """Generic fan controller class for an IPMI zone."""

    # Constant values for temperature calculation
    CALC_MIN: int = 0
    CALC_AVG: int = 1
    CALC_MAX: int = 2

    # Error messages.
    ERROR_MSG_FILE_IO: str = 'Cannot read file ({}).'

    # Configuration parameters
    log: Log                # Reference to a Log class instance
    ipmi: Ipmi              # Reference to an Ipmi class instance
    ipmi_zone: int          # IPMI zone identifier
    name: str               # Name of the controller
    count: int              # Number of controlled entities
    temp_calc: int          # Calculate of the temperature (0-min, 1-avg, 2-max)
    steps: int              # Discrete steps in temperatures and fan levels
    sensitivity: float      # Temperature change to activate fan controller (C)
    polling: float          # Polling interval to read temperature (sec)
    min_temp: float         # Minimum temperature value (C)
    max_temp: float         # Maximum temperature value (C)
    min_level: int          # Minimum fan level (0..100%)
    max_level: int          # Maximum fan level (0..100%)
    hwmon_path: List[str]   # Paths for hwmon files in sysfs

    # Measured or calculated attributes
    temp_step: float        # A temperature steps value (C)
    level_step: float       # A fan level step value (0..100%)
    last_time: float        # Last system time we polled temperature (timestamp)
    last_temp: float        # Last measured temperature value (C)
    last_level: int         # Last configured fan level (0..100%)

    # Function variable for selected temperature calculation method
    get_temp_func: Callable[[], float]

    def __init__(self, log: Log, ipmi: Ipmi, ipmi_zone: int, name: str, count: int, temp_calc: int, steps: int,
                 sensitivity: float, polling: float, min_temp: float, max_temp: float, min_level: int,
                 max_level: int, hwmon_path: str) -> None:
        """Initialize the FanController class. Will raise an exception in case of invalid parameters.

        Args:
            log (Log): reference to a Log class instance
            ipmi (Ipmi): reference to an Ipmi class instance
            ipmi_zone (int): IPMI zone identifier
            name (str): name of the controller
            count (int): number of controlled entities
            temp_calc (int): calculation of temperature
            steps (int): discrete steps in temperatures and fan levels
            sensitivity (float): temperature change to activate fan controller (C)
            polling (float): polling time interval for reading temperature (sec)
            min_temp (float): minimum temperature value (C)
            max_temp (float): maximum temperature value (C)
            min_level (int): minimum fan level value [0..100%]
            max_level (int): maximum fan level value [0..100%]
            hwmon_path (str): multiple path elements in sys/hwmon files (it could be a multi-line string value)
        """
        # Save and validate configuration parameters.
        self.log = log
        self.ipmi = ipmi
        self.ipmi_zone = ipmi_zone
        if self.ipmi_zone not in {Ipmi.CPU_ZONE, Ipmi.HD_ZONE}:
            raise ValueError('invalid value: ipmi_zone')
        self.name = name
        self.count = count
        if self.count <= 0:
            raise ValueError('count <= 0')
        self.temp_calc = temp_calc
        if self.temp_calc not in {self.CALC_MIN, self.CALC_AVG, self.CALC_MAX}:
            raise ValueError('invalid value: temp_calc')
        self.steps = steps
        if self.steps <= 0:
            raise ValueError('steps <= 0')
        self.sensitivity = sensitivity
        if self.sensitivity <= 0:
            raise ValueError('sensitivity <= 0')
        self.polling = polling
        if self.polling < 0:
            raise ValueError('polling < 0')
        if max_temp < min_temp:
            raise ValueError('max_temp < min_temp')
        self.min_temp = min_temp
        self.max_temp = max_temp
        if max_level < min_level:
            raise ValueError('max_level < min_level')
        self.min_level = min_level
        self.max_level = max_level
        # Set the proper temperature function.
        if self.count == 1:
            self.get_temp_func = self.get_1_temp
        else:
            self.get_temp_func = self.get_avg_temp
            if self.temp_calc == self.CALC_MIN:
                self.get_temp_func = self.get_min_temp
            elif self.temp_calc == self.CALC_MAX:
                self.get_temp_func = self.get_max_temp
        # Build hwmon_path list.
        self.build_hwmon_path(hwmon_path)
        # Check if temperature can be read successfully.
        if hwmon_path:
            try:
                self.get_temp_func()
            except IOError as e:
                raise e
        # Initialize calculated and measured values.
        self.temp_step = (max_temp - min_temp) / steps
        self.level_step = (max_level - min_level) / steps
        self.last_temp = 0
        self.last_level = 0
        self.last_time = time.monotonic() - (polling + 1)
        # Print configuration at DEBUG log level.
        if self.log.log_level >= self.log.LOG_DEBUG:
            self.log.msg(self.log.LOG_DEBUG, f'{self.name} fan controller was initialized with:')
            self.log.msg(self.log.LOG_DEBUG, f'   IPMI zone = {self.ipmi_zone}')
            self.log.msg(self.log.LOG_DEBUG, f'   count = {self.count}')
            self.log.msg(self.log.LOG_DEBUG, f'   temp_calc = {self.temp_calc}')
            self.log.msg(self.log.LOG_DEBUG, f'   steps = {self.steps}')
            self.log.msg(self.log.LOG_DEBUG, f'   sensitivity = {self.sensitivity}')
            self.log.msg(self.log.LOG_DEBUG, f'   polling = {self.polling}')
            self.log.msg(self.log.LOG_DEBUG, f'   min_temp = {self.min_temp}')
            self.log.msg(self.log.LOG_DEBUG, f'   max_temp = {self.max_temp}')
            self.log.msg(self.log.LOG_DEBUG, f'   min_level = {self.min_level}')
            self.log.msg(self.log.LOG_DEBUG, f'   max_level = {self.max_level}')
            self.log.msg(self.log.LOG_DEBUG, f'   hwmon_path = {self.hwmon_path}')
            self.print_temp_level_mapping()

    def build_hwmon_path(self, hwmon_str: str) -> None:
        """Build hwmon_path[] list for the specific zone."""

        # Convert the string into a list of path elements (respecting multi-line strings and wild characters).
        if hwmon_str:
            if "\n" in hwmon_str:
                self.hwmon_path = hwmon_str.splitlines()
            else:
                self.hwmon_path = hwmon_str.split()
            # Check the size of the hwmon_path[] list.
            if len(self.hwmon_path) != self.count:
                raise ValueError(f'Inconsistent count ({self.count}) and size of hwmon_path ({len(self.hwmon_path)})')
            # Convert wildcard characters if needed and check file existence.
            for i in range(self.count):
                if '?' in self.hwmon_path[i] or '*' in self.hwmon_path[i]:
                    file_names = glob.glob(self.hwmon_path[i])
                    if not file_names:
                        raise ValueError(self.ERROR_MSG_FILE_IO.format(self.hwmon_path[i]))
                    self.hwmon_path[i] = file_names[0]
                if not os.path.isfile(self.hwmon_path[i]):
                    raise ValueError(self.ERROR_MSG_FILE_IO.format(self.hwmon_path[i]))

    def get_1_temp(self) -> float:
        """Get a single temperature of the controlled entities in the IPMI zone. Can raise IOError exception."""
        value: float    # Float value to calculate the temperature.

        try:
            with open(self.hwmon_path[0], "r", encoding="UTF-8") as f:
                value = float(f.read()) / 1000
        except (IOError, FileNotFoundError) as e:
            raise e
        return value

    def get_min_temp(self) -> float:
        """Get the minimum temperature of the controlled entities in the IPMI zone. Can raise IOError exception.

           Returns:
                float: minimum temperature of controlled entities (C)
        """
        minimum: float = 1000.0     # Minimum temperature value
        value: float                # Float value

        # Calculate minimum temperature.
        try:
            for i in self.hwmon_path:
                with open(i, "r", encoding="UTF-8") as f:
                    value = float(f.read()) / 1000
                    if value < minimum:
                        minimum = value
        except (IOError, FileNotFoundError) as e:
            raise e
        return minimum

    def get_avg_temp(self):
        """Get average temperature of the controlled entities in the IPMI zone. Can raise IOError exception.

        Returns:
            float: average temperature of the controlled entities (C)
        """
        average: float = 0      # Average temperature
        counter: int = 0        # Value counter

        # Calculate average temperature.
        try:
            for i in self.hwmon_path:
                with open(i, "r", encoding="UTF-8") as f:
                    average += float(f.read()) / 1000
                    counter += 1
        except (IOError, FileNotFoundError) as e:
            raise e
        return average / counter

    def get_max_temp(self) -> float:
        """Get the maximum temperature of the controlled entities in the IPMI zone. Can raise IOError exception.

           Returns:
                float: maximum temperature of the controlled entities (C)
        """
        maximum: float = -1.0   # Maximum temperature
        value: float            # Temperature value

        # Calculate minimum temperature.
        try:
            for i in self.hwmon_path:
                with open(i, "r", encoding="UTF-8") as f:
                    value = float(f.read()) / 1000
                    if value > maximum:
                        maximum = value
        except (IOError, FileNotFoundError) as e:
            raise e
        return maximum

    def set_fan_level(self, level: int) -> None:
        """Set the new fan level in an IPMI zone. Can raise exception (ValueError).

        Args:
            level (int): new fan level [0..100]
        Returns:
            int: result (Ipmi.SUCCESS, Ipmi.ERROR)
        """
        return self.ipmi.set_fan_level(self.ipmi_zone, level)

    def callback_func(self) -> None:
        """Call-back function for a child class."""

    def run(self) -> None:
        """Run IPMI zone controller function with the following steps:

            * Step 1: Read current time. If the elapsed time is bigger than the polling time period
              then go to step 2, otherwise return.
            * Step 2: Read the current temperature. If the change of the temperature goes beyond
              the sensitivity limit then go to step 3, otherwise return
            * Step 3: Calculate the current gain and fan level based on the measured temperature
            * Step 4: If the new fan level is different it will be set and logged
        """
        current_time: float     # Current system timestamp (measured)
        current_temp: float     # Current temperature (measured)
        current_level: int      # Current fan level (calculated)
        current_gain: int       # Current gain level (calculated)

        # Step 1: check the elapsed time.
        current_time = time.monotonic()
        if current_time - self.last_time < self.polling:
            return
        self.last_time = current_time
        # Step 2: read temperature and sensitivity gap.
        self.callback_func()
        current_temp = self.get_temp_func()
        self.log.msg(self.log.LOG_DEBUG, f'{self.name}: new temperature > {current_temp:.1f}C')
        if abs(current_temp - self.last_temp) < self.sensitivity:
            return
        self.last_temp = current_temp
        # Step 3: calculate gain and fan level.
        if current_temp <= self.min_temp:
            current_gain = 0
            current_level = self.min_level
        elif current_temp >= self.max_temp:
            current_gain = self.steps
            current_level = self.max_level
        else:
            current_gain = int(round((current_temp - self.min_temp) / self.temp_step))
            current_level = int(round(float(current_gain) * self.level_step)) + self.min_level
        # Step 4: the new fan level will be set and logged.
        if current_level != self.last_level:
            self.last_level = current_level
            self.set_fan_level(current_level)
            self.log.msg(self.log.LOG_INFO, f'{self.name}: new level > {current_temp:.1f}C > '
                         f'[T:{self.min_temp+(current_gain*self.temp_step)}C/L:{current_level}%]')

    def print_temp_level_mapping(self) -> None:
        """Print out the uder-defined temperature to level mapping value in log DEBUG level."""
        self.log.msg(self.log.LOG_DEBUG, '   Temperature:level mapping:')
        for i in range(self.steps + 1):
            self.log.msg(self.log.LOG_DEBUG, f'   {i}. [T:{self.min_temp+(i*self.temp_step):.1f}C - '
                         f'L:{int(self.min_level + (i * self.level_step))}%]')


class CpuZone(FanController):
    """CPU zone fan control."""

    CPU_ZONE_TAG: str = 'CPU zone'  # CPU zone chapter name in the configuration file.

    def __init__(self, log: Log, ipmi: Ipmi, config: configparser.ConfigParser) -> None:
        """Initialize the CpuZone class and raise exception in case of invalid configuration.

        Args:
            log (Log): reference to a Log class instance
            ipmi (Ipmi): reference to an Ipmi class instance
            config (configparser.ConfigParser): reference to the configuration (default=None)
        """

        # Initialize arrays with proper size.
        count = config[self.CPU_ZONE_TAG].getint('count', fallback=1)
        if count <= 0:
            raise ValueError('count <= 0')
        self.hwmon_path = [''] * count

        # Initialize FanController class.
        super().__init__(
            log, ipmi, Ipmi.CPU_ZONE, 'CPU zone', count,
            config[self.CPU_ZONE_TAG].getint('temp_calc', fallback=FanController.CALC_AVG),
            config[self.CPU_ZONE_TAG].getint('steps', fallback=6),
            config[self.CPU_ZONE_TAG].getfloat('sensitivity', fallback=3.0),
            config[self.CPU_ZONE_TAG].getfloat('polling', fallback=2),
            config[self.CPU_ZONE_TAG].getfloat('min_temp', fallback=30.0),
            config[self.CPU_ZONE_TAG].getfloat('max_temp', fallback=60.0),
            config[self.CPU_ZONE_TAG].getint('min_level', fallback=35),
            config[self.CPU_ZONE_TAG].getint('max_level', fallback=100),
            config[self.CPU_ZONE_TAG].get('hwmon_path')
        )

    def build_hwmon_path(self, hwmon_str: str) -> None:
        """Build hwmon_path[] list for the CPU zone."""
        path: str               # Path string
        file_names: List[str]   # Result list of glob.glob()

        # If the user specified the hwmon_path= configuration item.
        if hwmon_str:
            # Convert the string into a list of path.
            super().build_hwmon_path(hwmon_str)
        # If the hwmon_path string was not specified it will be created automatically.
        else:
            # Construct hwmon_path with the resolution of wildcard characters.
            for i in range(self.count):
                path = '/sys/devices/platform/coretemp.' + str(i) + '/hwmon/hwmon*/temp1_input'
                file_names = glob.glob(path)
                if not file_names:
                    raise ValueError(self.ERROR_MSG_FILE_IO.format(path))
                self.hwmon_path[i] = file_names[0]


class HdZone(FanController):
    """Class for HD zone fan control."""

    # HdZone specific parameters.
    hd_device_names: List[str]          # Device names of the hard disks (e.g. '/dev/disk/by-id/...').
    HD_ZONE_TAG: str = 'HD zone'        # HD zone chapter name in the configuration file.

    # Standby guard specific parameters.
    standby_guard_enabled: bool         # Standby guard feature enabled
    standby_hd_limit: int               # Number of HDs in STANDBY state before the full RAID array will go STANDBY
    smartctl_path: str                  # Path for 'smartctl' command
    standby_flag: bool                  # The actual state of the whole HD array
    standby_change_timestamp: float     # Timestamp of the latest change in STANDBY mode
    standby_array_states: List[bool]    # Standby states of HDs

    # Error message.
    ERROR_MSG_SMARTCTL: str = 'Unknown smartctl return value {}'

    def __init__(self, log: Log, ipmi: Ipmi, config: configparser.ConfigParser) -> None:
        """Initialize the HdZone class. Abort in case of configuration errors.

        Args:
            log (Log): reference to a Log class instance
            ipmi (Ipmi): reference to an Ipmi class instance
            config (configparser.ConfigParser): reference to the configuration (default=None)
        """
        count: int      # HD count
        hd_names: str   # String for hd_names=

        # Read count parameter.
        count = config[self.HD_ZONE_TAG].getint('count', fallback=1)
        if count <= 0:
            raise ValueError('count <= 0')
        # Initialize lista with proper size.
        self.hd_device_names = [''] * count
        self.hwmon_path = [''] * count
        # Save and validate further HdZone class specific parameters.
        hd_names = config[self.HD_ZONE_TAG].get('hd_names')
        if not hd_names:
            raise ValueError('Parameter hd_names= is not specified.')
        if "\n" in hd_names:
            self.hd_device_names = hd_names.splitlines()
        else:
            self.hd_device_names = hd_names.split()
        if len(self.hd_device_names) != count:
            raise ValueError(f'Inconsistent count ({count}) and size of hd_names ({len(self.hd_device_names)})')
        # Initialize FanController class.
        super().__init__(
            log, ipmi, Ipmi.HD_ZONE, "HD zone", count,
            config[self.HD_ZONE_TAG].getint('temp_calc', fallback=FanController.CALC_AVG),
            config[self.HD_ZONE_TAG].getint('steps', fallback=4),
            config[self.HD_ZONE_TAG].getfloat('sensitivity', fallback=2),
            config[self.HD_ZONE_TAG].getfloat('polling', fallback=10),
            config[self.HD_ZONE_TAG].getfloat('min_temp', fallback=32),
            config[self.HD_ZONE_TAG].getfloat('max_temp', fallback=46),
            config[self.HD_ZONE_TAG].getint('min_level', fallback=35),
            config[self.HD_ZONE_TAG].getint('max_level', fallback=100),
            config[self.HD_ZONE_TAG].get('hwmon_path')
        )
        # Read and validate the configuration of standby guard if enabled.
        self.standby_guard_enabled = config[self.HD_ZONE_TAG].getboolean('standby_guard_enabled', fallback=False)
        if self.count == 1:
            self.log.msg(self.log.LOG_INFO, 'Standby guard is disabled << [HD zone] count=1')
            self.standby_guard_enabled = False
        if self.standby_guard_enabled:
            self.standby_array_states = [False] * count
            # Read and validate further parameters.
            self.standby_hd_limit = config[self.HD_ZONE_TAG].getint('standby_hd_limit', fallback=1)
            if self.standby_hd_limit < 0:
                raise ValueError('standby_hd_limit < 0')
            if self.standby_hd_limit > self.count:
                raise ValueError('standby_hd_limit > count')
            self.smartctl_path = config[self.HD_ZONE_TAG].get('smartctl_path', '/usr/sbin/smartctl')
            # Get the current power state of the HD array.
            try:
                n = self.check_standby_state()
            except Exception as e:
                raise e
            self.standby_change_timestamp = time.monotonic()
            self.standby_flag = n == self.count
        # Print configuration in DEBUG log level (or higher).
        if self.log.log_level >= self.log.LOG_DEBUG:
            self.log.msg(self.log.LOG_DEBUG, f'   hd_names = {self.hd_device_names}')
            if self.standby_guard_enabled:
                self.log.msg(self.log.LOG_DEBUG, '   Standby guard is enabled:')
                self.log.msg(self.log.LOG_DEBUG, f'   standby_hd_limit = {self.standby_hd_limit}')
                self.log.msg(self.log.LOG_DEBUG, f'   smartctl_path = {self.smartctl_path}')
            else:
                self.log.msg(self.log.LOG_DEBUG, '   Standby guard is disabled')

    def build_hwmon_path(self, hwmon_str: str) -> None:
        """Build hwmon_path[] list for the HD zone."""
        hwmon_dir: str              # hwmon directory
        path: str                   # Path string
        hd_sata_names: List[str]    # SATA HD names
        sn: str                     # String for a sata name
        file_names: List[str]       # Result list for glob.glob()

        # If the user specified a hwmon_path= configuration item.
        if hwmon_str:
            # Convert the string into a string array (respecting multi-line strings).
            super().build_hwmon_path(hwmon_str)
        # If the hwmon_path string is not given it will be created automatically.
        else:
            # Initialize hd_sata_names[] array
            hd_sata_names = [''] * self.count
            # Find SATA names for specific hd_names= references.
            for i in range(self.count):
                if 'by-id' not in self.hd_device_names[i]:
                    raise ValueError(f'Invalid hd_names={self.hd_device_names[i]}, name is not'
                                     f'in \'/dev/disk/by-id\' form.')
                if not os.path.islink(self.hd_device_names[i]):
                    raise ValueError(f'Invalid hd_names={self.hd_device_names[i]}, the reference is not link.')
                hd_sata_names[i] = os.path.basename(os.readlink(self.hd_device_names[i]))
            # Find the proper sys/hwmon files based on SATA names.
            hwmon_dir = '/sys/class/scsi_disk'
            for f in os.listdir(hwmon_dir):
                sn = os.path.join(hwmon_dir, f, 'device/block/sd*')
                file_names = glob.glob(sn)
                if not file_names:
                    raise ValueError(self.ERROR_MSG_FILE_IO.format(sn))
                sn = os.path.basename(file_names[0])
                try:
                    index = hd_sata_names.index(sn)
                except ValueError:
                    # It means that we found an unknown SATA name, and we can skip it
                    # because this disk is not part of the configuration.
                    continue
                path = os.path.join(hwmon_dir, f, 'device/hwmon/hwmon*/temp1_input')
                file_names = glob.glob(path)
                if not file_names:
                    raise ValueError(self.ERROR_MSG_FILE_IO.format(path))
                self.hwmon_path[index] = file_names[0]
            # Check the size of hwmon_path array
            if len(self.hwmon_path) != self.count:
                raise ValueError(f'Invalid hd_names= parameter, not all hwmon files was found ({self.hwmon_path})')

    def callback_func(self) -> None:
        """Call-back function execute standby guard."""
        if self.standby_guard_enabled:
            self.run_standby_guard()

    def get_standby_state_str(self) -> str:
        """Get a string representing the power state of the HD array with a character.

        Returns:
            str:   standby state string where all HD represented with a character (A-ACTIVE, S-STANDBY)
        """
        result: str = ''    # Result string

        for i in range(self.count):
            if self.standby_array_states[i]:
                result += 'S'
            else:
                result += 'A'
        return result

    def check_standby_state(self):
        """Check the actual power state of the HDs in the array and store them in 'standby_states'.

        Returns:
            int:   number of HDs in STANDBY mode
        """
        r: subprocess.CompletedProcess      # Result of the executed process.

        # Check the current power state of the HDs
        for i in range(self.count):
            self.standby_array_states[i] = False
            r = subprocess.run([self.smartctl_path, '-i', '-n', 'standby', self.hd_device_names[i]],
                               check=False, capture_output=True, text=True)
            if r.returncode not in {0, 2}:
                raise Exception(self.ERROR_MSG_SMARTCTL.format(r.returncode))
            if str(r.stdout).find("STANDBY") != -1:
                self.standby_array_states[i] = True
        return self.standby_array_states.count(True)

    def go_standby_state(self):
        """Put active HDs to STANDBY state in the array (based on the actual state of 'standby_states').
        """
        r: subprocess.CompletedProcess      # Result of the executed process.

        # Iterate through HDs list
        for i in range(self.count):
            # if the HD is ACTIVE
            if not self.standby_array_states[i]:
                # then move it to STANDBY state
                r = subprocess.run([self.smartctl_path, '-s', 'standby,now', self.hd_device_names[i]],
                                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if r.returncode != 0:
                    raise Exception(self.ERROR_MSG_SMARTCTL.format(r.returncode))
                self.standby_array_states[i] = True

    def run_standby_guard(self):
        """Monitor changes in the power state of an HD array and help them to move to STANDBY state together.
        This feature is implemented in the following way:
            * step 1: Checks the power state of all HDs in array
            * step 2: Check if the array is going to STANDBY (i.e. was ACTIVE before and reached the limit with number
                      of HDs in STANDBY state), put the remaining active members to STANDBY state and log the event
            * step 3: Check if the array is waking up (i.e. was in STANDBY state before and there is any ACTIVE
                      HD(s) in the array) and log the event
        """
        hds_in_standby: int     # HDs in standby mode
        minutes: float          # Elapsed time in minutes
        cur_time: float         # New timestamp for STANDBY change

        # Step 1: check the current power state of the HD array
        hds_in_standby = self.check_standby_state()
        cur_time = time.monotonic()

        # Step 2: check if the array is going to STANDBY state.
        if not self.standby_flag and hds_in_standby >= self.standby_hd_limit:
            minutes = (cur_time - self.standby_change_timestamp) / float(3600)
            self.log.msg(self.log.LOG_INFO, f'Standby guard: Change ACTIVE to STANDBY after {minutes:.1f} hour(s)'
                         f'[{self.get_standby_state_str()}]')
            self.go_standby_state()
            self.standby_flag = True
            self.standby_change_timestamp = cur_time

        # Step 3: check if the array is waking up.
        elif self.standby_flag and hds_in_standby < self.count:
            minutes = (cur_time - self.standby_change_timestamp) / float(3600)
            self.log.msg(self.log.LOG_INFO, f'Standby guard: Change STANDBY to ACTIVE after {minutes:.1f} hour(s)'
                         f'[{self.get_standby_state_str()}]')
            self.standby_flag = False
            self.standby_change_timestamp = cur_time


def main():
    """Main function: starting point of the systemd service."""
    my_parser: argparse.ArgumentParser      # Instance for an ArgumentParser class
    my_results: argparse.Namespace          # Results of parsed command line arguments
    my_config: configparser.ConfigParser    # Instance for a parsed configuration
    my_log: Log                             # Instance for a Log class
    my_ipmi: Ipmi                           # Instance for an Ipmi class
    my_cpu_zone: CpuZone                    # Instance for a CPU Zone fan controller class
    my_hd_zone: HdZone                      # Instance for an HD Zone fan controller class
    old_mode: int                           # Old IPMI fan mode
    cpu_zone_enabled: bool                  # CPU zone fan controller enabled
    hd_zone_enabled: bool                   # HD zone fan controller enabled

    # Parse the command line arguments.
    my_parser = argparse.ArgumentParser()
    my_parser.add_argument('-c', action='store', dest='config_file', default='smfc.conf',
                           help='configuration file')
    my_parser.add_argument('-v', action='version', version='%(prog)s ' + version_str)
    my_parser.add_argument('-l', type=int, choices=[0, 1, 2, 3], default=1,
                           help='log level: 0-NONE, 1-ERROR(default), 2-INFO, 3-DEBUG')
    my_parser.add_argument('-o', type=int, choices=[0, 1, 2], default=2,
                           help='log output: 0-stdout, 1-stderr, 2-syslog(default)')
    my_results = my_parser.parse_args()

    # Create a Log class instance (in theory this cannot fail).
    try:
        my_log = Log(my_results.l, my_results.o)
    except ValueError as e:
        print(f'ERROR: {e}.', flush=True, file=sys.stdout)
        sys.exit(5)

    if my_log.log_level >= my_log.LOG_DEBUG:
        my_log.msg(my_log.LOG_DEBUG, 'Command line arguments:')
        my_log.msg(my_log.LOG_DEBUG, f'   original arguments: {" ".join(sys.argv[:])}')
        my_log.msg(my_log.LOG_DEBUG, f'   parsed config file = {my_results.config_file}')
        my_log.msg(my_log.LOG_DEBUG, f'   parsed log level = {my_results.l}')
        my_log.msg(my_log.LOG_DEBUG, f'   parsed log output = {my_results.o}')

    # Parse and load configuration file.
    my_config = configparser.ConfigParser()
    if not my_config or not my_config.read(my_results.config_file):
        my_log.msg(my_log.LOG_ERROR, f'Cannot load configuration file ({my_results.config_file})')
        sys.exit(6)
    my_log.msg(my_log.LOG_DEBUG, f'Configuration file ({my_results.config_file}) loaded')

    # Create an Ipmi class instances and set required IPMI fan mode.
    try:
        my_ipmi = Ipmi(my_log, my_config)
        old_mode = my_ipmi.get_fan_mode()
    except (ValueError, FileNotFoundError) as e:
        my_log.msg(my_log.LOG_ERROR, f'{e}.')
        sys.exit(7)
    my_log.msg(my_log.LOG_DEBUG, f'Old IPMI fan mode = {my_ipmi.get_fan_mode_name(old_mode)}')
    if old_mode != my_ipmi.FULL_MODE:
        my_ipmi.set_fan_mode(my_ipmi.FULL_MODE)
        my_log.msg(my_log.LOG_DEBUG, f'New IPMI fan mode = {my_ipmi.get_fan_mode_name(my_ipmi.FULL_MODE)}')

    # Create an instance for CPU zone fan controller if enabled.
    my_cpu_zone = None
    cpu_zone_enabled = my_config['CPU zone'].getboolean('enabled', fallback=False)
    if cpu_zone_enabled:
        my_log.msg(my_log.LOG_DEBUG, 'CPU zone fan controller enabled')
        my_cpu_zone = CpuZone(my_log, my_ipmi, my_config)

    # Create an instance for HD zone fan controller if enabled.
    my_hd_zone = None
    hd_zone_enabled = my_config['HD zone'].getboolean('enabled', fallback=False)
    if hd_zone_enabled:
        my_log.msg(my_log.LOG_DEBUG, 'HD zone fan controller enabled')
        my_hd_zone = HdZone(my_log, my_ipmi, my_config)

    # Calculate the default sleep time for the main loop.
    if cpu_zone_enabled and hd_zone_enabled:
        wait = min(my_cpu_zone.polling, my_hd_zone.polling) / 2
    elif cpu_zone_enabled and not hd_zone_enabled:
        wait = my_cpu_zone.polling / 2
    elif not cpu_zone_enabled and hd_zone_enabled:
        wait = my_hd_zone.polling / 2
    else:  # elif not cpu_zone_enabled and not hd_controller_enabled:
        my_log.msg(my_log.LOG_ERROR, 'None of the fan controllers are enabled, service terminated.')
        sys.exit(8)
    my_log.msg(my_log.LOG_DEBUG, f'Main loop wait time = {wait} sec')

    # Main execution loop.
    while True:
        if cpu_zone_enabled:
            my_cpu_zone.run()
        if hd_zone_enabled:
            my_hd_zone.run()
        time.sleep(wait)


if __name__ == '__main__':
    main()
