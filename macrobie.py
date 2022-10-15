#!/usr/bin/env python3

  # Documentation.md
  # Upload
  # Comment and email

"""
Macrobie by James Hofmann, 
based on Keebie by Robin Universe & friends

![Macrobie logo](macrobie-logo.svg)

Macrobie is an Python 3 extension to Autokey for secondary keyboards.

Macrobie features:

* Device detection and configuration
* UI to map bindings to devices
* Triggers for autokey-run, a powerful system for macro scripting
* CSV-based configuration format (use a spreadsheet app to map large numbers of shortcuts)
* Multiple layers support: each binding is mapped to a layer and can trigger a layer switch

To use this script, the calling user must meet these requirements:

1. Access to /dev/input so that evdev can read devices:
  Under Ubuntu this is done by running `sudo adduser $USERNAME input` and logging out.
2. A running process of AutoKey in the background. autokey-run is called to access script data.
  Running macrobie under sudo disrupts access to Dbus, making autokey-run unable to access autokey.

Run with 
  `python3 macrobie.py` 
All program UI is menu-driven, just follow the prompts.

Configuration data is saved in 
  `~/.config/macrobie`

If you need to add a lot of bindings, try opening your configuration in a CSV editor!

The format starts with a line identifying the device:
`device,<format version>,<detection type>,<device's reported name>,<device's reported physical address>`

Then lines of bindings:
`binding,<layer>,<event type>,<event value>,<trigger type>,<trigger value>`

The device always launches in "default" layer; Macrobie will read bindings in top-to-bottom order
when triggering, and it will trigger multiple actions if you've bound them to the same key.
This can be used to, for example, create additional behavior while setting the active layer.

Most events will be of type "keydown". You may have a reason to use "scandown" instead,
in which case you'll need to get the scancode value. 
  
2022-9-21 

Some of the technical assumptions of Macrobie:
  
1. The user running macrobie must be added to the "input" group and log in again so that evdev can see and poll devices and grab their hardware info. 
This has some consequences for security(more permissions means now every app could poll your input hardware, if it thought to try), 
but addressing them is above what I'm aiming for, and it means macrobie runs without ever asking for a password.

2. The user must also have an instance of autokey open so that autokey-run will work. autokey-run uses dbus to trigger autokey, 
and dbus imposes a same-user requirement(i.e. sudo can't use autokey-run, or at least, it can't without magic that I am unaware of) - this is why the 
input group is used instead of just calling macrobie with sudo to let evdev work.

3. When macrobie is run it presents a shell menu with options to add 
and configure devices. Right now all it does is walk through the add device wizard, prompts for which keys you want bound, and then immediately goes 
to a test that triggers a "hello world" when the bound keys are pressed. Everything is done with evdev.

4. Event triggers are performed by calling autokey-run, which has three modes: script, phrase, folder popup. These map to the titles of scripts, 
phrases, and folders stored within Autokey. So, there's no need to manage the content of the triggered macro within macrobie, it just needs to supply 
a matching title.

5. Macrobie's functionality is just in finding and polling the different devices, and mapping the events to a 
binding. Earlier I tested making a udev rule and now I use evdev to do the same things. The udev
code is still alive, unused, in DeviceForm.

6. The matching rules I've chosen(name + local address or complete physical address) are intended to help with devices 
that expose multiple addresses with the same name. In general, only one address actually matters to using a device.
There might be a reason to attempt merging events across the devices instead.

7. The devices chosen for loading are grabbed by macrobie at the start of the event loop, so they can't be used for normal typing.
  
I believe the next things to do would be:

1. Look into splitting out the processes so that macrobie's inner loop runs in sudo, rather than requiring input group.
2. Look into incorporating autokey-run's functionality so that it doesn't have to call out to another process.

2022-10-14

Checklist for testing Macrobie functionality:

1. Startup behaviors
  *1a. No config data(DebugWipeConfig)
  *1b. Existing config data
  1c. Extraneous config data
  1d. Unreadable config data
2. Add device
  *2a. Add a new device
  *2b. Quit without saving after adding the device and verify that it was not written
  *2c. Save and verify that the save identified the device with the correct data
  *2c.1. Identified by name and local address
  *2c.2. Identified by full USB address
  *2c.3. Both name and full address
  *2d. Add two of the same device and make sure they are disambiguated correctly.
3. Remove device
  *3a. Attempt to remove devices when none are added
  *3b. Remove device successfully
  *3c. Verify that device removal was saved
4. Edit device
  *4a. Add a new binding
  *4a.1. Add a phrase binding
  *4a.2. Add a script binding
  *4a.3. Add a folder binding
  *4a.4. Add a layer change binding
  *4b. Remove a binding, then cancel. Verify that it still exists.
  *4c. Remove a binding, then save. Verify that it was removed.
5. Run
  *5a. Verify phrase binding
  *5b. Verify script binding
  *5c. Verify folder binding
  *5d. Verify layer change binding
  *5e. Can we exit cleanly?
  
"""

from evdev import InputDevice, categorize, ecodes, KeyEvent, InputEvent, UInput
import evdev
import os
import sys
import argparse
import time
import subprocess
import math
from pathlib import Path

import csv
import json

DebugWipeConfig = False # Enable to automatically blow away your config for testing. Use with care

if 'SUDO_USER' in os.environ:
  dataDir = Path("/home/" + os.environ['SUDO_USER']) / Path(".config/macrobie") # Path where user configuration files should be stored
else:
  dataDir = Path("/home/" + os.environ['USER']) / Path(".config/macrobie") # Path where user configuration files should be stored

deviceDir = dataDir / "devices" # Cache the full path to the /devices directory

class MenuInput(object):
  def __init__(self, choice_list=(("Choice One","action one"),("Choice Two","action two"))):
    self.choices = choice_list
    self.default_choice = 1;
    self.pre_text = None
    self.post_text = None
  def render_choices(self):
    if self.pre_text:
      print(self.pre_text)
    for idx in range(len(self.choices)):
      print("-"+str(idx+1)+": "+self.choices[idx][0])
    if self.post_text:
      print(self.post_text)
  def choose(self, choice_text):
    choice_num = -1
    if choice_text == "":
      choice_num = self.default_choice
    else:
      choice_num = int(choice_text)
    if choice_num < 1 or choice_num > len(self.choices):
      print(f'Please choose between 1 and {len(self.choices)}.')
      raise Exception("Invalid choice")
    return self.choices[choice_num - 1][1]
  def choice_loop(self):
    self.render_choices()
    while True:
      try:
        return self.choose(input(f'Please make your selection({self.default_choice}): '))
      except: # Not valid yet
        pass

class PaginatedMenuInput(object):
  def __init__(self, choice_list=(("Choice One","action one"),("Choice Two","action two"))):
    self.choices = choice_list
    self.default_choice = 1;
    self.current_page = 0
    self.rows_in_page = 20
    self.pre_text = None
    self.post_text = None
  def render_choices(self):
    if self.pre_text:
      print(self.pre_text)
    highest_idx = 0
    lowest_idx = self.current_page * self.rows_in_page
    total_pages = math.ceil(len(self.choices) / self.rows_in_page)
    self.choice_next = None
    self.choice_prev = None
    for idx in range(self.rows_in_page):
      oidx = idx + self.current_page * self.rows_in_page
      if oidx < len(self.choices):
        print("-"+str(idx+1)+": "+self.choices[oidx][0])
        highest_idx = oidx
    if highest_idx < len(self.choices) - 1:
      self.choice_next = self.current_page + 1
    else:
      self.choice_next = 0
    if lowest_idx > 0:
      self.choice_prev = self.current_page - 1
    else:
      self.choice_prev = total_pages - 1
    print("Page "+str(self.current_page)+" -n: Next Page -p: Previous Page")
    if self.post_text:
      print(self.post_text)
  def choose(self, choice_text):
    choice_num = -1
    if choice_text == "n":
      self.current_page = self.choice_next
      return None
    elif choice_text == "p":
      self.current_page = self.choice_prev
      return None
    elif choice_text == "":
      choice_num = self.default_choice
    else:
      try:
        choice_num = int(choice_text)
      except:
        return None
    if choice_num < 1 or choice_num > len(self.choices):
      print(f'Please choose between 1 and {len(self.choices)}.')
      raise Exception("Invalid choice")
    ans = self.current_page * self.rows_in_page + (choice_num - 1)
    return self.choices[ans][1]
  def choice_loop(self):
    self.render_choices()
    while True:
      ans = self.choose(input(f'Please make your selection({self.default_choice}): '))
      if ans is None:
        self.render_choices()
      else:
        return ans
        
def detectDevice(instructions):
  global all_devices
    
  time.sleep(0.5) # some debounce time to avoid stray inputs
  print(instructions)
  
  for d in all_devices:
    n = d.read_one()
    while n is not None:
      n = d.read_one() # clear inputs
    d.grab()
  
  dev = None
  while dev is None:
    for d in all_devices:
      n = d.read_one()
      while n is not None:
        if n is not None and n.type == 1: # only take key events (no mouse, axis, LED mapping)
          dev = d
        n = d.read_one() # clear inputs
    time.sleep(0.01)
  for d in all_devices:
    d.ungrab()
  print(dev.path, dev.name, dev.phys)
  
  # a moment to clear buffer
  time.sleep(0.25)  
  
  return dev

class DeviceSearch(object):

  def __init__(self):
    self.version = "version-1"
    self.search_type = "name"
    self.name = None
    self.phys = None
  def rRow(self, row):
    if row[0] != "device":
      raise Exception("Attempted to read a DeviceSearch row where none exists: "+str(row))
    self.version = row[1]
    self.search_type = row[2]
    self.name = row[3]
    self.phys = row[4]
  def wRow(self):
    return ["device",self.version,self.search_type,self.name,self.phys]
  def shell(self):
    mi = MenuInput([("Use the device name and local address","name"),("Use the complete USB physical address","phys"),("Use both","both")])
    mi.pre_text = "How should this device be detected?"
    search_type = mi.choice_loop()
    
    add_device = detectDevice("Press a key on the device to be added:")
    
    self.name = add_device.name
    self.phys = add_device.phys
    self.search_type = search_type
    
  def get(self):

    global all_devices
    if self.search_type == "name":
      phys_last = self.phys.split("/")[-1]
      for n in all_devices:
        if n.name == self.name and n.phys.endswith(phys_last):
          return n
    elif self.search_type == "phys":
      for n in all_devices:
        if n.phys == self.phys:
          return n
    else: # "both"
      for n in all_devices:
        if n.name == self.name and n.phys == self.phys:
          return n

  # passthroughs to the initialized device
  def grab(self):
    return self.get().grab()
  def ungrab(self):
    return self.get().ungrab()
  def read_one(self):
    return self.get().read_one()
    
  def __str__(self):
    return "(version)"+self.version+"(search_type) "+self.search_type+" (name) "+self.name+\
    " / (phys) "+self.phys+" "+self.phys
  def __eq__(self, other):
    return self.search_type == other.search_type and self.phys == other.phys and \
    self.name == other.name

class BindingRow(object):

  def __init__(self):
    self.layer = "default"
    self.event_type = None
    self.event_data = None
    self.trigger_type = None
    self.trigger_data = None
  
  def wKeyDown(self, key): # evdev keycode
    self.event_type = "keydown"
    self.event_data = key
    
  def wScanDown(self, key): # evdev scancode
    self.event_type = "scandown"
    self.event_data = key
    
  def wScript(self, script): # Autokey script trigger
    self.trigger_type = "script"
    self.trigger_data = script
    
  def wPhrase(self, phrase): # Autokey phrase trigger
    self.trigger_type = "phrase"
    self.trigger_data = phrase
    
  def wFolder(self, folder): # Autokey folder trigger
    self.trigger_type = "folder"
    self.trigger_data = folder
    
  def wAssignLayer(self, layer): # assign layer state to name
    self.trigger_type = "assign_layer"
    self.trigger_data = layer
    
  def wLayer(self, layer): # set the layer on which this binding is active
    self.layer = layer
    
  def rEcode(self): # read the matching ecode, if any
    pass # TODO
        
  def rKey(self): # read the evdev key id
    pass # TODO
    
  def eventMatch(self, evdev_event): # review an input event to see if it matches this row's event
    if type(evdev_event) is KeyEvent:
      if self.event_type == "keydown":
        return evdev_event.keycode == self.event_data and evdev_event.keystate == KeyEvent.key_down
      elif self.event_type == "scandown":
        return evdev_event.scancode == self.event_data and evdev_event.keystate == KeyEvent.key_down
    return False
    
  def trigger(self, parent_device):
    if self.trigger_type == "phrase":
      subprocess.run(["autokey-run","-p",self.trigger_data],capture_output=True,text=True)
    elif self.trigger_type == "script":
      subprocess.run(["autokey-run","-s",self.trigger_data],capture_output=True,text=True)
    elif self.trigger_type == "folder":
      subprocess.run(["autokey-run","-f",self.trigger_data],capture_output=True,text=True)
    elif self.trigger_type == "assign_layer":
      parent_device.layer = self.trigger_data
      print("set layer to "+self.trigger_data)
    
  def wRow(self): # write out a row as a list
    return ["binding", self.layer, self.event_type, self.event_data, self.trigger_type, self.trigger_data]
    
  def rRow(self, v): # read in a list as a row
    self.layer = v[1]
    self.event_type = v[2]
    self.event_data = v[3]
    self.trigger_type = v[4]
    self.trigger_data = v[5]

  def shell(self, device, trigger_type = "phrase", trigger_data = "hello"):
    # polls a device for an event, and then tries to get the keycode and assign a default trigger; 
    # if it fails to assign the event a keycode, it gets the scancode instead.
 
    time.sleep(0.5)
    
    n = device.read_one()
    while n is not None:
      n = device.read_one() # clear inputs
    device.grab()
    print("Press the key to bind:")
    ans = None
    while ans is None:
      n = device.read_one()
      if n is not None and n.type == 1: # only take key events (no mouse, axis, LED mapping)
        ans = n
      time.sleep(0.01)
    device.ungrab()
    
    key = evdev.util.categorize(ans)
    if key.keycode is not None and type(key.keycode) is str:
      self.wKeyDown(key.keycode)
    else:
      self.wScanDown(key.scancode)
    self.trigger_type = trigger_type
    self.trigger_data = trigger_data
    print(self)
    
    time.sleep(0.25)
    
  def __str__(self):
    return "(layer) "+self.layer+" / (event) "+self.event_type+" "+self.event_data+\
    " / (trigger) "+self.trigger_type+" "+self.trigger_data
  
  def choosable(self):
    return (str(self), self)

  def copy(self):
    ans = BindingRow()
    ans.layer = self.layer
    ans.event_type = self.event_type 
    ans.event_data = self.event_data 
    ans.trigger_type = self.trigger_type
    ans.trigger_data = self.trigger_data
    return ans

  def __eq__(self, other):
    return other.layer == self.layer and other.event_type == self.event_type and \
    other.event_data == self.event_data and other.trigger_type == self.trigger_type and \
    other.trigger_data == self.trigger_data
    
class DeviceTable(object):

  """It's a container for bindings mapping a hardware device to script triggers or layer changes.
  
  The table is written as a CSV so that it can be manipulated as tabular data in spreadsheet programs, which can directly copy-paste tables from HTML.
  This is intended to help with mass binding of shortcut keys in popular applications.
  """
  
  def __init__(self):
    self.filename = "new-device"
    self.search = DeviceSearch()
    self.layer = "default" # active layer state
    self.binding = []

  # Passthroughs to the initialized device
  def grab(self):
    return self.search.grab()
  def ungrab(self):
    return self.search.ungrab()
  def read_one(self):
    return self.search.read_one()
    
  def eventMatch(self, evdev_event):
    ans = []
    for n in self.binding:
      if n.layer == self.layer:
        if n.eventMatch(evdev_event):
          print(n)
          n.trigger(self)
          ans.append(n)
    return ans

  def rCsv(self, fpath = 'testbinding.csv'):
    with open(fpath) as csv_file:
      self.filename = str(Path(fpath).stem)
      self.binding = []
      csv_reader = csv.reader(csv_file, delimiter=',')
      for row in csv_reader:
        if len(row) > 0:
          if row[0] == "binding":
            ans = BindingRow()
            ans.rRow(row)
            self.binding.append(ans)
          elif row[0] == "device":
            ans = DeviceSearch()
            ans.rRow(row)
            self.search = ans
      return ans
    return None

  def wCsv(self, fpath = 'testbinding2.csv'):
  
    with open(fpath, 'w') as csv_file:
      csv_writer = csv.writer(csv_file, delimiter=',')
      csv_writer.writerow(self.search.wRow())
      for row in self.binding:
        csv_writer.writerow(row.wRow())
      return True
    return None
  
  def roundtrip_test(self, fpath = "testbinding.csv"):
    print("roundtripping "+self.filename)
    self.wCsv(fpath)
    ans = DeviceTable()
    ans.rCsv(fpath)
    if ans.search != self.search:
      print("search mismatch: ")
      print(self.search)
      print("was read back as:")
      print(ans.search)
    for idx in range(len(ans.binding)):
      if ans.binding[idx] != self.binding[idx]:
        print("Binding #"+str(idx))
        print(self.binding[idx])
        print("was read back as:")
        print(ans.binding)

  def add_binding_shell(self):
    addc = (("Trigger Phrase","phrase"),("Trigger Folder","folder"),
      ("Trigger Script","script"),("Assign Layer","assign_layer"),("Cancel","cancel"))
    addmenu = MenuInput(addc)
    
    ch = addmenu.choice_loop()
    
    ans = BindingRow()
   
    if ch == "phrase":
      title = input(f'Enter the title of the phrase in Autokey (e.g "First phrase")>')
      if len(title) < 1:
        title = "First phrase"
      ans.shell(self, "phrase", title)
    elif ch == "folder":
      title = input(f'Enter the title of the folder in Autokey (e.g "My Phrases")>')
      if len(title) < 1:
        title = "My Phrases"
      ans.shell(self, "folder", title)
    elif ch == "script":
      title = input(f'Enter the title of the script in Autokey (e.g "List Menu")>')
      if len(title) < 1:
        title = "List Menu"
      ans.shell(self, "script", title)
    elif ch == "assign_layer":
      title = input(f'Enter the title of the layer to transition the device to (e.g "default")>')
      if len(title) < 1:
        title = "default"
      ans.shell(self, "assign_layer", title)
    else:
      return

    default_layer = input(f'Enter the layer this binding is active in(hit enter for "default")>')
    if default_layer is None or len(default_layer) < 1:
      ans.layer = "default"
    else:
      ans.layer = default_layer
      
    self.binding.append(ans)
    
  def shell(self, ch="main"):
    
    mmc = (("Add Binding","add_binding"),
    ("Remove Binding","remove_binding"),("OK","save"),("Cancel","cancel"))
    mainmenu = MenuInput(mmc)
    mainmenu.pre_text = "Editing "+self.filename
    
    backup = [n.copy() for n in self.binding]
    
    while ch is not None:
      if ch == "main":
        mainmenu.post_text = str(len(self.binding))+" binding(s) assigned."
        ch = mainmenu.choice_loop()
      elif ch == "add_binding":
        self.add_binding_shell()
        ch = "main"
      elif ch == "remove_binding":
        blist = [n.choosable() for n in self.binding]
        blist.append(("Cancel","cancel"))
        removec = PaginatedMenuInput(blist).choice_loop()
        ch = "main"
        if type(removec) is str and removec == "cancel":
          pass
        else:
          self.binding.remove(removec)
      elif ch == "save":
        ch = None
      elif ch == "cancel":
        self.binding = backup
        ch = None
        
def write_config_directories():
  for path in [dataDir, deviceDir]:
    if not os.path.exists(path):
      print("wrote directory: "+str(path))
      os.makedirs(path)
def del_config_directories():
  import shutil
  if os.path.exists(dataDir) and os.path.exists(deviceDir):
    shutil.rmtree(dataDir)
    print("deleted all config data")
  else:
    print("did not delete config, uncertain directory state. Please manually check "+str(dataDir))
def disambig(want,have):
  # FIXME maybe generalize it to a larger range more elegantly
  dtab = [want]
  for n in range(2, 100):
    dtab.append(want+"-"+(str(n)))
  for n in have:
    dtab.remove(n)
  return dtab[0]
  
def save_config(devices, files_to_destroy):
  files_to_add = []
  for d in devices:
    if d.filename in files_to_add: # impending collision
      d.filename = disambig(d.filename, files_to_add) # rename it
    if d.filename in files_to_destroy: # if we're gonna save it...
      files_to_destroy.remove(d.filename) # don't remove it.
    files_to_add.append(d.filename)
    dpath = str(deviceDir / Path(d.filename+".csv")) 
    d.wCsv(dpath)
    print("wrote "+dpath)
  for d in files_to_destroy:
    dpath = str(deviceDir / Path(d+".csv"))
    os.remove(dpath)
    print("Deleted "+dpath)
  print("Saved "+str(len(devices))+" devices.")
def load_config():
  ans = []
  print(deviceDir)
  for n in list(deviceDir.glob('**/*.csv')):
    print("loading "+str(n))
    t = DeviceTable()
    t.rCsv(deviceDir / n)
    ans.append(t)
  return ans
    
def menus():
  mmc = (("Save and Run","run"),("Add Device","add_device"),("Edit Device","edit_device"),("Remove Device","remove_device"),("Save and quit","savequit"),("Quit without saving","cancelquit"))
  mainmenu = MenuInput(mmc)
  
  slc = (("Add shell command","add_shell"),("Add keystroke sequence","add_keystroke"),("Remove binding","remove_binding"),("Done","main"))
  selected_layer = MenuInput(slc)

  loaded_devices = load_config() # DeviceTable() instances
  
  global all_devices

  current_device = None
  files_to_destroy = []
  
  ch = "main"
  while ch:
    for n in loaded_devices:
      n.roundtrip_test()
    if ch == "main":
      if len(loaded_devices) > 0:
        mainmenu.pre_text = "Devices found:\n" + "\n".join([n.filename for n in loaded_devices])
      else:
        mainmenu.pre_text = "No devices configured."
      ch = mainmenu.choice_loop()
    elif ch == "run":
      save_config(loaded_devices, files_to_destroy)
      
      time.sleep(0.5) # some debounce time to avoid stray inputs
      for d in loaded_devices:
        d.grab()
        e = d.read_one() # drain events
        while e is not None:
          e = d.read_one()
      print("Starting run loop. Ctrl+C to exit.")
      while True:
        for device in loaded_devices:
          e = device.read_one()
          while e is not None:
            cat = evdev.util.categorize(e)
            device.eventMatch(cat)
            e = device.read_one()
        time.sleep(0.01)
      #for d in devices:
        #d.ungrab()
      #print(dev.path, dev.name, dev.phys)
      
    elif ch == "add_device":
      dt = DeviceTable()
      dt.search.shell()
      dt.filename = input("What name should I give this device? ("+dt.search.name+") >")
      if len(dt.filename) < 1:
        dt.filename = dt.search.name
      loaded_devices.append(dt)
      current_device = dt
      ch = "edit_device"
    elif ch == "edit_device":
      if not current_device:
        device_choices = [(n.filename, n) for n in loaded_devices]
        device_choices.append(("Cancel","cancel"))
        m = PaginatedMenuInput(device_choices)
        m.pre_text = "Choose the device to edit:"
        current_device = m.choice_loop()
      if not current_device == "cancel":
        current_device.shell()
      current_device = None
      ch = "main"
    elif ch == "remove_device":
      if not current_device:
        device_choices = [(n.filename, n) for n in loaded_devices]
        device_choices.append(("Cancel","cancel"))
        m = PaginatedMenuInput(device_choices)
        m.pre_text = "Choose the device to remove:"
        current_device = m.choice_loop()
      if not current_device == "cancel":
        loaded_devices.remove(current_device)
        files_to_destroy.append(current_device.filename)
      current_device = None
      ch = "main"
    elif ch == "savequit":
      save_config(loaded_devices, files_to_destroy)
      print("Done")
      return
    elif ch == "cancelquit":
      print("Done. No save was made.")
      return
  print("Exited main loop")
  return

def cleanup_devices():
  for n in all_devices:
    try:
      n.ungrab()
    except:
      pass
    try:
      n.close()
    except:
      pass
  
if __name__=="__main__":

  if DebugWipeConfig:
    del_config_directories()
  write_config_directories()
  
  global all_devices
  all_devices = [evdev.InputDevice(n) for n in evdev.list_devices()] # grab all devices
  try:
    menus()
    cleanup_devices()
  except:
    import traceback
    print(traceback.format_exc())
    cleanup_devices()

###################################### Unused/Historical:

























class DeviceForm(object):

    """A container for all device configuration data, including source data and compiled outputs.
    It's a "form" because you can fill it out like one.
    
    This is my first attempt at DeviceForm, which compiled udev rules, before I went for a full-evdev approach.
    
    Keeping it here in case a need for udev returns.
    """

    # Constants
    
    ## Prompts for wizard behaviors, indicating to the UI layer what we're requesting.
    prompt_infopath = "infopath"
    prompt_unmappable = "unmappable"
    prompt_devicename = "devicename"
    prompt_ownername = "ownername"
    prompt_devkeysearch = "devkeysearch"
    prompt_bindingfile = "binding"
    prompt_scriptfile = "script"

    ## Default mechanisms for udev attribute search.
    search_all = ["ATTRS{id/product}","ATTRS{id/vendor}","ATTRS{phys}"]
    search_product_vendor = ["ATTRS{id/product}","ATTRS{id/vendor}"]
    search_phys = ["ATTRS{phys}"]
    
    def __init__(self):
        self.infopath = None # The original path that udev/the user indicated as an event source.
          # NOTE: store paths as str, not Path(). Otherwise they don't serialize.
        self.devkey = {} # The device attributes that are being used by our udev rule.
        self.devkeysearch = None # The search parameters that determined our devkey attributes.
        self.devicename = None # The (user-defined) name of the device.
        self.ownername = None # The user declared as the owner of the event file generated by udev.
        self.rule = None # The source text of the resulting udev rule.
        self.devices = None # A summary of what was output from "udevadm info <self.infopath>".
        self.binding = None # The binding we use for this device.
        self.script = None # The script we use for this device.
        self.device_format_version = "2" # When the format makes a breaking change, please increment this

    def get_info(self):
    
        # capture the info from udevadm and turn it into a data structure
    
        cap = subprocess.run(["sudo","udevadm","info","-a",self.infopath], capture_output=True,text=True)
        
        print(self.infopath)
        devices = []
        for line in cap.stdout.split("\n"):
            ls = line.strip()
            if ls.startswith("looking at"):
                devices.append({})
            elif len(devices)>0:
                if len(line)>1:
                    lsplit = line.split("==")
                    devices[-1][lsplit[0].strip()]=lsplit[1].strip()
        self.devices = devices
        
    def get_devkey(self):
    
        # apply devkeysearch to generate candidates for udev rules
        
        # scan the devices from the child node up to the parent
        # and record the first candidate for each devkey we look at
        
        # this is split out from rule compilation so that we can interact
        # with the search process in an interactive setting. 
        
        # An alternative architecture: allow compilation to fully complete or fail on every change.
        # That consolidates compilation into a process returning a larger amount of info.
        
        self.devkey = {}
        for candidate_key in self.devkeysearch:            
            for device in self.devices:
                if candidate_key in device and not candidate_key in self.devkey:
                    self.devkey[candidate_key] = device[candidate_key]


    def compile_rule(self):
    
        # use the recorded devkey data to create a udev rule
        devkeylist = []
        for k in self.devkey:
            devkeylist.append(k+"=="+self.devkey[k])

        ans = """ACTION=="add", KERNEL=="event[0-9]*", SUBSYSTEM=="input", """
        ans += ", ".join(devkeylist)+", " 
        ans += """SYMLINK+="macrobie-"""+self.devicename+"""-%k", """
        ans += 'OWNER="'+self.ownername+'"'
        self.rule = ans

    def make_editor(self):
        choices = []
        choices.append(("Infopath", DeviceForm.prompt_infopath))
        choices.append(("Device name", DeviceForm.prompt_devicename))
        choices.append(("Owner name", DeviceForm.prompt_ownername))
        choices.append(("Device search mode", DeviceForm.prompt_devkeysearch))
        choices.append(("Binding File", DeviceForm.prompt_bindingfile))
        choices.append(("Script File", DeviceForm.prompt_scriptfile))
        choices.append(("OK", True))
        choices.append(("Cancel", False))
        ##### TODO: Also consider reporting the state of each attr in the choice prompt.
        return MenuInput(choices)
    def act_editor(self, prompt):
        print("Set "+prompt+" from "+getattr(self, prompt)+" to:")
        ans = input(">")
        if len(ans) > 0:
          setattr(self, prompt, ans)
          print("Set.")
        else:
          print("Cancelled.")
        
    """Apply various rules to suggest what to prompt next in a wizard-style form entry."""
    def wizard_tick(self):
        if self.infopath == None:
            return DeviceForm.prompt_infopath
        elif self.devices == None:
            self.get_info()
        if self.devicename == None:
            return DeviceForm.prompt_devicename
        elif self.ownername == None:
            return DeviceForm.prompt_ownername
        elif self.devkeysearch == None:
            return DeviceForm.prompt_devkeysearch
        self.get_devkey()
        if len(self.devkey)<1:
            return DeviceForm.prompt_unmappable
        if self.binding == None:
            return DeviceForm.prompt_bindingfile
        if self.script == None:
            return DeviceForm.prompt_scriptfile
        self.compile_rule()
        return None
        
    def save(self):
        src = {}
        ans = {}
        data = {"src":src,"ans":ans}
        for n in ["infopath","devkeysearch","devicename","ownername","binding","script","device_format_version"]:
          setattr(src, n, getattr(self,n))
        for n in ["devkey","rule","devices"]:
          setattr(ans, n, getattr(self,n))
        return data
        
    def load(data):
        w = DeviceForm()
        src = data["src"]
        ans = data["ans"]
        for n in ["infopath","devkeysearch","devicename","ownername","binding","script","device_format_version"]:
          setattr(w, n, src[n])
        for n in ["devkey","rule","devices"]:
          setattr(w, n, ans[n])
        return w
        
    def roundtrip_test(self):
        js = self.save()
        w = DeviceForm.load(js)
        ans = {}
        for n in ["infopath","devkeysearch","devicename","ownername","binding","script","devkey","rule","devices"]:
          ans[n] = getattr(self, n) == getattr(w, n)
        return ans
        
