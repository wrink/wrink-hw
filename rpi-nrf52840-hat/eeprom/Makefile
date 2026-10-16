all: wrink-pi.eep

# Keep the compiled overlay around instead of deleting it as an intermediate
.SECONDARY: wrink-pi.dtbo

%.dtbo: %.dts
	dtc -@ -I dts -O dtb -o $@ $<

# -v1: legacy HAT format; HAT+ (the default) doesn't support setgpio
%.eep: %.txt %.dtbo
	eepmake -v1 $*.txt $*.eep $*.dtbo

clean:
	rm -f *.eep *.dtbo

flash:
	eepflash.sh -w -f=wrink-pi.eep -t=24c32 -a=50

.PHONY: all clean flash
