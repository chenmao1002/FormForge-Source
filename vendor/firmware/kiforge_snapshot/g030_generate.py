"""Fixed STM32G030F6P6 reset-entry examples; no imported model code or flashing."""


def g030_files(req):
    task=req['task'];baud=req.get('baud',115200);period=req.get('period_ms',1000)
    main='''#include "stm32g030xx.h"
#include <stdint.h>
/* Only normal reset entry with factory HSI trim; no bootloader handover. */
uint32_t SystemCoreClock=16000000U;
static volatile uint32_t ticks_ms;
volatile uint32_t kiforge_fault;
volatile uint32_t kiforge_uart_rx_errors;
void SysTick_Handler(void) { ticks_ms++; }
static void halt(uint32_t reason) {
    kiforge_fault |= reason;
    for (;;) { __WFI(); }
}
void SystemInit(void) {
    RCC->CR |= RCC_CR_HSION | RCC_CR_HSIKERON;
    uint32_t guard=1000000U;
    while (!(RCC->CR & RCC_CR_HSIRDY)) { if (--guard==0U) halt(1U); }
    RCC->CFGR=(RCC->CFGR & ~(RCC_CFGR_SW|RCC_CFGR_HPRE|RCC_CFGR_PPRE)) | RCC_CFGR_SW_HSISYS;
    guard=1000000U;
    while ((RCC->CFGR & RCC_CFGR_SWS)!=RCC_CFGR_SWS_HSISYS) { if (--guard==0U) halt(1U); }
    RCC->CR &= ~RCC_CR_HSIDIV; /* HSI16 / 1, no PLL, factory trim unchanged. */
    __DSB(); __ISB();
    SystemCoreClock=16000000U;
}
static void start_tick(void) { if (SysTick_Config(16000U)!=0U) halt(1U); }
static void delay_ms(uint32_t delay) {
    uint32_t start=ticks_ms;
    while ((uint32_t)(ticks_ms-start)<delay) { __WFI(); }
}
'''
    if task in ('uart_echo','i2c_probe_uart'):
        main+=f'''static void uart_init(void) {{
    RCC->IOPENR |= RCC_IOPENR_GPIOAEN; (void)RCC->IOPENR;
    RCC->APBENR1 |= RCC_APBENR1_USART2EN; (void)RCC->APBENR1;
    RCC->APBRSTR1 |= RCC_APBRSTR1_USART2RST; RCC->APBRSTR1 &= ~RCC_APBRSTR1_USART2RST;
    /* Physical pads9/10 = PA2/PA3, USART2 AF1. No SWD alternate route. */
    GPIOA->OTYPER &= ~((1U<<2)|(1U<<3));
    GPIOA->OSPEEDR &= ~((3U<<4)|(3U<<6)); GPIOA->PUPDR &= ~((3U<<4)|(3U<<6));
    GPIOA->AFR[0]=(GPIOA->AFR[0]&~((15U<<8)|(15U<<12)))|(1U<<8)|(1U<<12);
    GPIOA->MODER=(GPIOA->MODER&~((3U<<4)|(3U<<6)))|(2U<<4)|(2U<<6);
    USART2->BRR={(16000000+baud//2)//baud}U;
    USART2->CR1=USART_CR1_UE|USART_CR1_TE|USART_CR1_RE;
}}
static int uart_putc(uint8_t byte) {{
    uint32_t start=ticks_ms;
    while (!(USART2->ISR & USART_ISR_TXE_TXFNF)) {{
        if ((uint32_t)(ticks_ms-start)>=20U) {{ kiforge_fault|=2U; return 0; }}
    }}
    USART2->TDR=byte; return 1;
}}
'''
    if task=='blink':
        main+=f'''int main(void) {{
    start_tick(); RCC->IOPENR|=RCC_IOPENR_GPIOAEN; (void)RCC->IOPENR;
    GPIOA->BSRR=1U<<16; /* PA0 latch low before enabling active-high LED output. */
    GPIOA->OTYPER&=~1U;GPIOA->OSPEEDR&=~3U;GPIOA->PUPDR&=~3U;
    GPIOA->MODER=(GPIOA->MODER&~3U)|1U;
    for (;;) {{ GPIOA->BSRR=1U;delay_ms({period//2}U);GPIOA->BSRR=1U<<16;delay_ms({period-period//2}U); }}
}}
'''
    elif task=='uart_echo':
        # Keep the tick helper used without introducing a busy-loop timing assumption.
        main+='''int main(void) {
    start_tick();uart_init();delay_ms(1U);
    for (;;) {
        uint32_t status=USART2->ISR;
        if (status & (USART_ISR_ORE|USART_ISR_FE|USART_ISR_NE|USART_ISR_PE)) {
            USART2->ICR=USART_ICR_ORECF|USART_ICR_FECF|USART_ICR_NECF|USART_ICR_PECF;
            (void)USART2->RDR;kiforge_uart_rx_errors++;continue;
        }
        if (status & USART_ISR_RXNE_RXFNE) {
            uint8_t byte=(uint8_t)USART2->RDR;
            if (!uart_putc(byte)) halt(2U);
        }
    }
}
'''
    elif task=='i2c_probe_uart':
        main+='''volatile uint8_t kiforge_i2c_ack_bitmap[16];
volatile uint32_t kiforge_i2c_scans, kiforge_i2c_nacks;
static int uart_text(const char *text) {
    while (*text) { if (!uart_putc((uint8_t)*text++)) return 0; }
    return 1;
}
static void i2c_init(void) {
    RCC->APBENR2|=RCC_APBENR2_SYSCFGEN;(void)RCC->APBENR2;
    /* Keep both aliases analog until type/AF/peripheral configuration is complete. */
    GPIOA->MODER|=(3U<<18)|(3U<<20)|(3U<<22)|(3U<<24);
    /* Pads16/17 default PA11/12. SET both bits to expose PA9/10. */
    SYSCFG->CFGR1|=SYSCFG_CFGR1_PA11_RMP|SYSCFG_CFGR1_PA12_RMP;
    GPIOA->OTYPER|=(1U<<9)|(1U<<10);
    GPIOA->OSPEEDR&=~((3U<<18)|(3U<<20));GPIOA->PUPDR&=~((3U<<18)|(3U<<20)|(3U<<22)|(3U<<24));
    GPIOA->AFR[1]=(GPIOA->AFR[1]&~((15U<<4)|(15U<<8)))|(6U<<4)|(6U<<8);
    RCC->APBENR1|=RCC_APBENR1_I2C1EN;(void)RCC->APBENR1;
    RCC->APBRSTR1|=RCC_APBRSTR1_I2C1RST;RCC->APBRSTR1&=~RCC_APBRSTR1_I2C1RST;
    RCC->CCIPR=(RCC->CCIPR&~RCC_CCIPR_I2C1SEL)|RCC_CCIPR_I2C1SEL_1; /* HSI16 kernel. */
    I2C1->CR1=0U; /* PE=0 while configuring; analog filter ON, DNF=0, stretch allowed. */
    /* Computed conservative Standard-mode setting, not measured/CubeMX-certified.
       PRESC3, SCLDEL5, SDADEL4, SCLH23, SCLL23; see profile timing assumptions. */
    I2C1->TIMINGR=0x30541717U;
    I2C1->CR2=0U;I2C1->OAR1=0U;I2C1->OAR2=0U;
    GPIOA->MODER=(GPIOA->MODER&~((3U<<18)|(3U<<20)))|(2U<<18)|(2U<<20);
    I2C1->CR1=I2C_CR1_PE;
}
static int probe(uint8_t address) {
    uint32_t start=ticks_ms;
    while (I2C1->ISR & I2C_ISR_BUSY) {
        if ((uint32_t)(ticks_ms-start)>=20U) { kiforge_fault|=4U;return -1; }
    }
    I2C1->ICR=I2C_ICR_NACKCF|I2C_ICR_STOPCF|I2C_ICR_BERRCF|I2C_ICR_ARLOCF|I2C_ICR_OVRCF|I2C_ICR_TIMOUTCF;
    /* Address-only write-direction transfer: NBYTES=0, no TXDR/data payload.
       This may mean SMBus Quick to some devices; use only a reviewed bus. */
    I2C1->CR2=((uint32_t)address<<1)|I2C_CR2_START|I2C_CR2_AUTOEND;
    int nack=0;start=ticks_ms;
    for (;;) {
        uint32_t status=I2C1->ISR;
        if (status & (I2C_ISR_BERR|I2C_ISR_ARLO|I2C_ISR_OVR|I2C_ISR_TIMEOUT)) { kiforge_fault|=16U;return -1; }
        if (status & I2C_ISR_NACKF) { nack=1;I2C1->ICR=I2C_ICR_NACKCF; }
        if (status & I2C_ISR_STOPF) { I2C1->ICR=I2C_ICR_STOPCF;return nack ? 0 : 1; }
        if ((uint32_t)(ticks_ms-start)>=20U) { kiforge_fault|=8U;return -1; }
    }
}
int main(void) {
    static const char hex[]="0123456789ABCDEF";
    start_tick();uart_init();i2c_init();
    if (!uart_text("I2C ADDRESS PROBE; DEVICE ID UNVERIFIED\\r\\n")) { I2C1->CR1=0U;halt(2U); }
    for (;;) {
        for (unsigned i=0;i<16U;i++) kiforge_i2c_ack_bitmap[i]=0U;
        for (unsigned address=0x08U;address<=0x77U;address++) {
            int result=probe((uint8_t)address);
            if (result<0) { I2C1->CR1=0U;(void)uart_text("I2C FAULT; STOPPED\\r\\n");halt(kiforge_fault); }
            if (result==0) { kiforge_i2c_nacks++;continue; }
            kiforge_i2c_ack_bitmap[address>>3]|=(uint8_t)(1U<<(address&7U));
            if (!uart_text("ACK 0x") || !uart_putc((uint8_t)hex[address>>4]) || !uart_putc((uint8_t)hex[address&15U]) || !uart_text("\\r\\n")) {
                I2C1->CR1=0U;halt(2U);
            }
        }
        kiforge_i2c_scans++;
        if (!uart_text("SCAN DONE\\r\\n")) { I2C1->CR1=0U;halt(2U); }
        delay_ms(5000U);
    }
}
'''
    else:raise ValueError('Unsupported G030 task')
    startup='''#include <stdint.h>
extern uint32_t _estack,_sidata,_sdata,_edata,_sbss,_ebss;
extern int main(void);extern void SystemInit(void);extern void SysTick_Handler(void);
static void Default_Handler(void) { for (;;) {} }
void Reset_Handler(void) {
    uint32_t *src=&_sidata;
    for (uint32_t *p=&_sdata;p<&_edata;) *p++=*src++;
    for (uint32_t *p=&_sbss;p<&_ebss;) *p++=0U;
    SystemInit();(void)main();for (;;) {}
}
/* G030 exact 46-word vector layout. Unlisted reserved slots remain zero. */
__attribute__((section(".isr_vector"),used))
void (*const vectors[46])(void)={
    [0]=(void (*)(void))(&_estack),[1]=Reset_Handler,[2]=Default_Handler,[3]=Default_Handler,
    [11]=Default_Handler,[14]=Default_Handler,[15]=SysTick_Handler,[16]=Default_Handler,
    [18 ... 23]=Default_Handler,[25 ... 30]=Default_Handler,[32]=Default_Handler,
    [35]=Default_Handler,[37 ... 44]=Default_Handler
};
'''
    linker='''ENTRY(Reset_Handler)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 32K
 RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 8K }
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS {
 .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
 .text : { *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH
 .ARM.exidx : { *(.ARM.exidx*) . = ALIGN(4); } > FLASH
 _sidata = LOADADDR(.data);
 .data : { . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT > FLASH
 .bss (NOLOAD) : { . = ALIGN(4); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .; } > RAM
 .stack_reserve (NOLOAD) : { . = ALIGN(8); . += 1024; } > RAM
}
ASSERT((_sidata & 3) == 0, "Flash data source must be word aligned")
ASSERT(((_sdata | _edata | _sbss | _ebss) & 3) == 0, "RAM initialization must be word aligned")
ASSERT(_ebss <= _estack - 1024, "At least 1KB stack headroom is required")
ASSERT(SIZEOF(.isr_vector) == 184, "G030 requires its exact 46-word vector table")
'''
    make='''# Official ST cmsis-device-g0/Include and Arm CMSIS_5/Core/Include only.
CC = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
CFLAGS = -mcpu=cortex-m0plus -mthumb -mfloat-abi=soft -O2 -std=gnu11 -Wall -Wextra -Werror -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns -ffunction-sections -fdata-sections -DSTM32G030xx -I$(CMSIS_DEVICE) -I$(CMSIS_CORE)
all: firmware.elf firmware.bin
firmware.elf: main.c startup.c linker.ld
	$(CC) $(CFLAGS) -nostdlib -Wl,--gc-sections -T linker.ld main.c startup.c -o $@
firmware.bin: firmware.elf
	$(OBJCOPY) -O binary $< $@
.PHONY: all
'''
    readme=f'''# STM32G030F6P6 reset-entry review project

Task: {task}. Exact TSSOP20, 32KB flash/8KB RAM, nominal HSI16 16MHz.
Build: make CMSIS_DEVICE=/path/to/cmsis-device-g0/Include CMSIS_CORE=/path/to/CMSIS/Core/Include
No flash or option-byte command is supplied. Reset entry/factory trim is required; arbitrary bootloader handover is unsupported.
Physical pad19 is PA14/PA15, shared SWCLK/BOOT0. Its pulldown does not verify option bytes or debugger behavior during reset; separate reset_boot_reviewed attestation is required.
SysTick uses its own interrupt and a 46-word G030 vector table with reserved slots zero; other external interrupts remain unconfigured.
USART2 uses PA2/PA3 AF1 (pads9/10), not debug pins. TX waits are bounded to 20ms; UART echo counts/discards receive errors.
I2C diagnostic, when selected, explicitly validates all four UART/I2C signal roles. It sets PA11/12 remap bits to expose PA9/10 AF6 I2C1 on pads16/17.
The computed timing uses HSI16, analog filtering and no digital filter; it assumes 15.5-16.5MHz, rise<=1us/fall<=300ns and <=200pF bus budget with4.7k pulls. These assumptions require review/measurement, not just compiler success.
The diagnostic reports ACK addresses over UART, not device identities, and does not accept UART commands. Address-only write-direction probes can have SMBus Quick meaning: only use a reviewed single-master bus and reviewed devices.
BUSY/timeout/BERR/ARLO stops probing permanently and releases the I2C peripheral; no automatic recovery pulses/reset retries. A regular NACK means absent, not a bus fault.
No hardware runtime, waveform, power, debugger or manufacturing certification is implied. Do not operate hardware solely because generation/compilation succeeds.
'''
    return {'main.c':main,'startup.c':startup,'linker.ld':linker,'Makefile':make,'README.md':readme}
