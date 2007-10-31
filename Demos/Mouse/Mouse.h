/*
             MyUSB Library
     Copyright (C) Dean Camera, 2007.
              
  dean [at] fourwalledcubicle [dot] com
      www.fourwalledcubicle.com

 Released under the GPL Licence, Version 3
*/

#ifndef TESTAPP_H
#define TESTAPP_H

	/* Includes */
		#include <avr/io.h>
		#include <avr/pgmspace.h>

		#include "Descriptors.h"

		#include "../../MyUSB/Drivers/USB/USB.h"            // Event functionality

		#include "../../MyUSB/Scheduler/Scheduler.h"        // Simple scheduler for task management
		#include "../../MyUSB/Drivers/USBKEY/Joystick.h"    // Joystick driver for the USBKEY
		#include "../../MyUSB/Drivers/USBKEY/Bicolour.h"    // Bicolour LEDs driver for the USBKEY
		
	/* Task Definitions */
		TASK(USB_Mouse_Report);

	/* Type Defines */
		typedef struct
		{
			uint8_t Button;
			uint8_t X;
			uint8_t Y;
		} USB_MouseReport_Data_t;

		typedef struct
		{
			uint8_t ReportData[0x34];
		} USB_HID_Report_Mouse_t;
	
	/* External Variables */
		extern USB_HID_Report_Mouse_t MouseReport;
		
	/* Event Handlers */
		HANDLES_EVENT(USB_CreateEndpoints);

#endif