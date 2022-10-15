![Macrobie logo](logo.svg)

Macrobie by James Hofmann, 
based on Keebie by Robin Universe & friends  

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
  - [x] 1a. No config data(DebugWipeConfig)
  - [x] 1b. Existing config data
  - [x] 1c. Extraneous config data
  - [x] 1d. Unreadable config data
2. Add device
  - [x] 2a. Add a new device
  - [x] 2b. Quit without saving after adding the device and verify that it was not written
  - [x] 2c. Save and verify that the save identified the device with the correct data
  - [x] 2c.1 Identified by name and local address
  - [x] 2c.2 Identified by full USB address
  - [x] 2c.3 Both name and full address
  - [x] 2d. Add two of the same device and make sure they are disambiguated correctly.
3. Remove device
  - [x] 3a. Attempt to remove devices when none are added
  - [x] 3b. Remove device successfully
  - [x] 3c. Verify that device removal was saved
4. Edit device
  - [x] 4a. Add a new binding
  - [x] 4a.1 Add a phrase binding
  - [x] 4a.2 Add a script binding
  - [x] 4a.3 Add a folder binding
  - [x] 4a.4 Add a layer change binding
  - [x] 4b. Remove a binding, then cancel. Verify that it still exists.
  - [x] 4c. Remove a binding, then save. Verify that it was removed.
5. Run
  - [x] 5a. Verify phrase binding
  - [x] 5b. Verify script binding
  - [x] 5c. Verify folder binding
  - [x] 5d. Verify layer change binding
  - [x] 5e. Can we exit cleanly?
