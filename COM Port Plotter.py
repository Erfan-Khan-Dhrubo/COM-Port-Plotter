
import tkinter as tk
from tkinter import ttk, messagebox
# ttk: Stands for Themed Tkinter Widgets. It provides modern-looking widgets like buttons, labels etc.
# messagebox: Module to show popups like alerts, errors, warnings, or info boxes.

import threading # Used to run tasks in parallel.
import queue
import time
import re

# Import matplotlib for embedding
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# pyserial for serial communication
try:
    import serial  # package that lets Python talk to serial ports
    import serial.tools.list_ports # This imports a helper submodule inside PySerial that lists all available serial  ports on your computer.
except Exception as e:
    serial = None
    print("pyserial not available. Install with: pip install pyserial")


# Helper: list available serial ports
def list_serial_ports():
    if serial is None:
        return []
    ports = serial.tools.list_ports.comports()
    #.comports() is a function that scans your system for all available serial (COM) ports.
    # It returns a list of ListPortInfo objects, not just strings.
    # p.device  # e.g. 'COM3' or '/dev/ttyUSB0'
    # p.description  # e.g. 'Arduino Uno (COM3)'
    # p.hwid  # e.g. 'USB VID:PID=2341:0043 SER=85434353035351'

    return [p.device for p in ports]


# ------------------------
# Serial Reader Thread
# ------------------------
class SerialReader(threading.Thread): # SerialReader is a child of threading.Thread. So it automatically gets all the functions and behavior of a Python thread — such as .start(), .run(), .join(), etc.
    # Thread that reads lines from a serial port and pushes parsed samples to a queue.

    def __init__(self, port, baudrate, data_queue, stop_event):
        super().__init__(daemon=True) # This calls the parent class constructor (threading.Thread.__init__) and sets daemon=True, which means:
        # This thread will automatically close when the main program ends.

        self.port = port
        self.baudrate = baudrate
        self.queue = data_queue
        self.stop_event = stop_event
        self.ser = None # it holds the serial port object created by pyserial

    def run(self):
        if serial is None:
            self.queue.put(("__error__", "pyserial not available"))
            # In Python, queue.Queue is a thread-safe data structure.
            # That means it’s designed so multiple threads can safely share data without interfering with each other.
            return

        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            # serial.Serial(...) is part of the pyserial library.
            # It tries to open the given COM port (e.g. "COM3") at the given baud rate (e.g. 9600).
            # The timeout=1 means:
            #       When reading from the port, if no data arrives within 1 second, it gives up and returns nothing instead of hanging forever.
        except Exception as e:
            self.queue.put(("__error__", str(e)))
            return

        try:
            while not self.stop_event.is_set(): # This starts an infinite loop that runs as long as the user hasn’t requested the thread to stop.
                try:
                    line = self.ser.readline().decode(errors="ignore").strip()
                    # self.ser.readline() --> Reads one line from the serial port — everything up to a newline \n.
                    # .decode(errors="ignore") --> Converts the raw bytes (like b"27.3,30.39\r\n") into a normal Python string.
                    # errors="ignore" skips any invalid byte sequences that can’t be decoded.
                    # .strip() --> Removes any whitespace, like \r (carriage return) or \n (newline).

                except Exception: # If any read error happens (for example, Arduino disconnected suddenly),
                    break           # the program breaks out of the loop, stopping the reading process safely.
                if not line: # If the Arduino sends a blank line (common with serial noise), it just ignores it and continues to the next loop cycle.
                    continue
                parsed = self.parse_line(line) # This function extracts numbers from the text
                if parsed is None:
                    continue
                self.queue.put(parsed)
        finally:
            try:
                self.ser.close() # Close the Port
            except Exception:
                pass

    @staticmethod # This means the function doesn’t depend on the class instance (self).
    def parse_line(line):

        try:
            match = re.search(r"v1\s*=\s*([-+]?\d*\.?\d+)\s*,\s*v2\s*=\s*([-+]?\d*\.?\d+)", line)
            # Uses a regular expression (regex) to look for a pattern in the string.
            if match:
                v1 = float(match.group(1))
                v2 = float(match.group(2))
                return (time.time(), v1, v2) # Adds the current timestamp using time.time()

            parts = line.replace(" ", "").split(",") # If the regex doesn’t match (for example, if Arduino sends plain 27.3,30.39),
            if len(parts) >= 2:
                v1 = float(parts[0])
                v2 = float(parts[1])
                return (time.time(), v1, v2)
        except Exception:
            return None



# Main Application GUI
class SerialPlotterApp:
    def __init__(self, root): # __init__ is called automatically when you create an object of this class.
        self.root = root # root is the main Tkinter window passed from outside.
        self.root.title("Arduino 2-Channel Plotter") # self.root.title() sets the window title.

        self.data_queue = queue.Queue() # thread-safe queue to exchange data between the background thread
        self.stop_event = threading.Event() # used to signal the background thread to stop gracefully.
        self.reader_thread = None # self.reader_thread = None means there’s currently no serial reading thread running
        # later it will start and stop a thread that continuously reads data from your Arduino.

        # Keep all data (no limit)
        self.xdata = [] # self.xdata: Stores the x-axis values for the plot (usually sample index or time).
        self.y1 = [] # self.y1: Stores the values for Channel 1 from Arduino.
        self.y2 = [] # self.y2: Stores the values for Channel 2 from Arduino.

        # No data detection
        self.no_data_counter = 0 # Counts how many consecutive update cycles have occurred without receiving new data.
        self.no_data_limit = 10  # 10 cycles * 100ms ~ 1 second
        # If the counter reaches this limit, the app will show "No data is coming" in the UI.

        # Status messages
        self.connection_status_var = tk.StringVar(value="Disconnected") # special Tkinter variable that automatically updates any widget (like Label) linked to it.
        self.data_status_var = tk.StringVar(value="No data") # Shows whether data is currently coming from Arduino. Initially "No data"

        self.create_widgets() # Builds the Tkinter GUI controls like COM port dropdown, Connect button, and status labels.
        self.create_plots() # Sets up the matplotlib figure and axes to show the two channels of data in real-time.

        self.update_interval_ms = 100  # redraw every 100 ms
        self.root.after(self.update_interval_ms, self.periodic_update)
        # self.root.after(delay_ms, func) is a Tkinter method to schedule a function call after a given delay.

    def create_widgets(self):
        frm = ttk.Frame(self.root) # creates a container inside the main window (self.root) to organize widgets horizontally.
        frm.pack(fill="x", padx=8, pady=8) # stretch the frame horizontally across the window.

        ttk.Label(frm, text="COM Port:").pack(side="left") # Creates a label "COM Port:" inside the frame in left
        self.port_cb = ttk.Combobox(frm, values=list_serial_ports(), width=15)
        # ttk.Combobox is a dropdown menu to select the serial port.
        # values=list_serial_ports() fills the dropdown with all available COM ports.
        self.port_cb.pack(side="left", padx=(4, 10))

        self.refresh_btn = ttk.Button(frm, text="Refresh", command=self.refresh_ports)
        self.refresh_btn.pack(side="left", padx=(4, 10))

        ttk.Label(frm, text="Baud:").pack(side="left", padx=(10, 0))
        self.baud_cb = ttk.Combobox(frm, values=["9600", "19200", "38400", "57600", "115200"], width=8)
        self.baud_cb.set("9600")
        self.baud_cb.pack(side="left", padx=(4, 10))

        self.connect_btn = ttk.Button(frm, text="Connect", command=self.toggle_connection)
        # When pressed toggle_connection method is called
        self.connect_btn.pack(side="left", padx=(4, 10))

        ttk.Label(frm, textvariable=self.connection_status_var).pack(side="left", padx=(10, 0))
        ttk.Label(frm, textvariable=self.data_status_var).pack(side="left", padx=(10, 0))

    def create_plots(self):
        self.fig = Figure(figsize=(8, 5), dpi=100) # Creates a matplotlib figure for plotting
        # figsize=(8, 5) → 8 inches wide, 5 inches tall. dpi=100 → resolution of 100 dots per inch.

        # Creates two subplots inside the figure
        self.ax1 = self.fig.add_subplot(211) # 211 → 2 rows, 1 column, first subplot (top plot).
        self.ax2 = self.fig.add_subplot(212, sharex=self.ax1) # 212 → 2 rows, 1 column, second subplot (bottom plot).
        # sharex=self.ax1 → bottom plot shares the same X-axis as the top plot.

        self.line1, = self.ax1.plot([], [], 'r-', label="Channel 1")
        self.line2, = self.ax2.plot([], [], 'b-', label="Channel 2")

        self.ax1.set_ylabel("Value 1")
        self.ax2.set_ylabel("Value 2")
        self.ax2.set_xlabel("Samples")

        # Enables grid lines for better readability.
        self.ax1.grid(True)
        self.ax2.grid(True)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        # self.fig is your matplotlib figure object — the thing that contains the two subplots (ax1 and ax2).
        # FigureCanvasTkAgg is a special bridge between Matplotlib and Tkinter.
        # It converts the Matplotlib figure into something Tkinter can display as a widget.
        # master=self.root means: “Put this figure inside the Tkinter main window (root).”


        self.canvas_widget = self.canvas.get_tk_widget()
        # After creating the canvas, we call .get_tk_widget() to get the actual Tkinter widget object for that canvas.
        # This gives you a widget that behaves like any other Tkinter widget (like a Label, Button, etc.) — so you can place it using .pack(), .grid(), etc.

        self.canvas_widget.pack(fill=tk.BOTH, expand=True)
        # fill=tk.BOTH → Stretch the widget horizontally and vertically to fill all available space
        # expand=True → If the window is resized, the canvas will expand to occupy extra space.

    def refresh_ports(self):
        ports = list_serial_ports()
        self.port_cb['values'] = ports
        if ports:
            self.port_cb.set(ports[0])

    def toggle_connection(self):
        # self.reader_thread holds the background serial reader thread (from SerialReader).
        # is_alive() checks if that thread is currently running.
        if self.reader_thread and self.reader_thread.is_alive():
            self.stop_reader() # safely stops and closes the serial connection)
        else:
            port = self.port_cb.get() # This fetches the selected COM port name from the dropdown menu
            baud = int(self.baud_cb.get())
            if not port:
                messagebox.showerror("Error", "Select a COM port.")
                return
            self.start_reader(port, baud)

    def start_reader(self, port, baud):
        # Before starting a new connection, it clears all old data from the graph.
        self.xdata.clear()
        self.y1.clear()
        self.y2.clear()
        self.no_data_counter = 0

        self.stop_event.clear()
        # stop_event is an instance of threading.Event() — a special synchronization object that acts like a flag shared between threads.
        # Event starts in a “not set” state (False).
        # You can call:
        # .set() → to mark it as True (meaning “stop now!”)
        # .clear() → to mark it as False (meaning “keep running”)
        # .is_set() → to check its current state.
        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()

        self.reader_thread = SerialReader(port, baud, self.data_queue, self.stop_event) # We get the thread object
        self.reader_thread.start() # The thread start
        self.connect_btn.config(text="Disconnect")
        self.connection_status_var.set(f"Connected to {port}")
        self.data_status_var.set("No data")

    def stop_reader(self):
        if self.stop_event:
            self.stop_event.set() # Stopping SerialReader thread
        if self.reader_thread:
            self.reader_thread.join(timeout=2.0) # This waits (up to 2 seconds) for the background thread to finish and shut down completely.
        self.reader_thread = None
        self.connect_btn.config(text="Connect")
        self.connection_status_var.set("Disconnected")
        self.data_status_var.set("No data")

    # This function runs every 100 ms
    # self.root.after(self.update_interval_ms, self.periodic_update)
    def periodic_update(self):
        updated = False # This flag keeps track of whether new data was received from the serial port during this cycle.
        while True:
            try:
                item = self.data_queue.get_nowait()
                # Queue has three items: (t1, 27.3, 30.4), (t2, 25.4, 32.7), (t3, 23.8, 35.3).
                # while True with get_nowait() will fetch all three in this cycle.
                # Once empty, it breaks, and the GUI can continue updating.
            except queue.Empty:
                break
            self.no_data_counter = 0
            if isinstance(item, tuple) and item and item[0] == "__error__": # Checks if item is a tuple (like ("__error__", "Some message"))
                self.connection_status_var.set(f"Error: {item[1]}")
                self.data_status_var.set("No data")
                self.stop_reader()
                return
            try:
                ts, v1, v2 = item
            except Exception:
                continue
            self.append_sample(ts, v1, v2)
            updated = True

        if not updated:
            self.no_data_counter += 1
            if self.no_data_counter >= self.no_data_limit:
                self.data_status_var.set("No data is coming")
        else:
            self.data_status_var.set("Data coming")

        if updated:
            self.redraw_plots()

        self.root.after(self.update_interval_ms, self.periodic_update)

    def append_sample(self, ts, v1, v2):
        self.xdata.append(len(self.xdata))
        self.y1.append(v1)
        self.y2.append(v2)

    def redraw_plots(self):
        # .set_data(x, y) updates the data that will be drawn on the plot.
        self.line1.set_data(self.xdata, self.y1)
        self.line2.set_data(self.xdata, self.y2)
        if self.xdata:
            self.ax1.set_xlim(0, len(self.xdata))
            self.ax2.set_xlim(0, len(self.xdata))
        if self.y1:
            self.ax1.set_ylim(min(self.y1) - 1, max(self.y1) + 1)
        if self.y2:
            self.ax2.set_ylim(min(self.y2) - 1, max(self.y2) + 1)
        self.canvas.draw_idle()
        # draw_idle() tells matplotlib to redraw the plot asynchronously without freezing the GUI.

    def on_close(self):
        self.stop_reader() # Calls the stop_reader() method to safely stop the serial reading thread.
        self.root.destroy() # destroy() terminates the Tkinter mainloop and cleans up all widgets.



# Run the application
def main():
    root = tk.Tk()
    app = SerialPlotterApp(root)
    app.refresh_ports()
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    # When the user clicks the “X” button, app.on_close() is called instead of immediately destroying the window.
    root.mainloop()
    # Starts the Tkinter main event loop.


if __name__ == "__main__":
    main()
