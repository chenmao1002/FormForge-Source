"""Project-authored normal-reset templates for exact STM32 profiles; no HAL."""
from .firmware_targets import get_target


def stm32_files(name, task, period=1000, baud=115200):
    p=get_target(name)
    if task not in p['templates']:raise ValueError('此型号尚未适配所选模板。')
    clock=p['clockHz']; family=p['family']
    if family=='g0':
        from importlib.util import spec_from_file_location, module_from_spec
        from .firmware_targets import ROOT
        spec=spec_from_file_location('ff_g030_seed',ROOT/'vendor/firmware/kiforge_snapshot/g030_generate.py')
        module=module_from_spec(spec);spec.loader.exec_module(module)
        main=module.g030_files(dict(task=task,period_ms=period,baud=baud))['main.c'].replace('stm32g030xx.h',p['header'])
    else:
        main=f'''#include "{p['header']}"
#include <stdint.h>
uint32_t SystemCoreClock={clock}U;
static volatile uint32_t ticks_ms;
volatile uint32_t kiforge_fault, kiforge_uart_rx_errors;
void SysTick_Handler(void) {{ ticks_ms++; }}
static void halt(uint32_t reason) {{ kiforge_fault|=reason; for (;;) {{ __WFI(); }} }}
void SystemInit(void) {{
    RCC->CR|=RCC_CR_HSION;
    uint32_t guard=1000000U;
    while (!(RCC->CR&RCC_CR_HSIRDY)) {{ if (--guard==0U) halt(1U); }}
    RCC->CFGR&=~(RCC_CFGR_SW|RCC_CFGR_HPRE|RCC_CFGR_PPRE1|RCC_CFGR_PPRE2);
    guard=1000000U;
    while (RCC->CFGR&RCC_CFGR_SWS) {{ if (--guard==0U) halt(1U); }}
    SCB->VTOR=0x08000000U; __DSB(); __ISB();
    SystemCoreClock={clock}U;
}}
static void start_tick(void) {{ if (SysTick_Config({clock//1000}U)!=0U) halt(1U); }}
static void delay_ms(uint32_t delay) {{
    uint32_t start=ticks_ms;
    while ((uint32_t)(ticks_ms-start)<delay) {{ __WFI(); }}
}}
'''
        gpio_clock='RCC->APB2ENR|=RCC_APB2ENR_IOPAEN; (void)RCC->APB2ENR;' if family=='f1' else 'RCC->AHB1ENR|=RCC_AHB1ENR_GPIOAEN; (void)RCC->AHB1ENR;'
        if task=='blink':
            config='GPIOA->CRL=(GPIOA->CRL&~15U)|2U;' if family=='f1' else 'GPIOA->OTYPER&=~1U; GPIOA->OSPEEDR&=~3U; GPIOA->PUPDR&=~3U; GPIOA->MODER=(GPIOA->MODER&~3U)|1U;'
            main+=f'''int main(void) {{
    start_tick(); {gpio_clock}
    GPIOA->BSRR=1U<<16; {config}
    for (;;) {{ GPIOA->BSRR=1U;delay_ms({period//2}U);GPIOA->BSRR=1U<<16;delay_ms({period-period//2}U); }}
}}
'''
        else:
            config='GPIOA->CRL=(GPIOA->CRL&~(255U<<8))|(10U<<8)|(4U<<12);' if family=='f1' else '''GPIOA->OTYPER&=~((1U<<2)|(1U<<3));
    GPIOA->OSPEEDR&=~((3U<<4)|(3U<<6)); GPIOA->PUPDR&=~((3U<<4)|(3U<<6));
    GPIOA->AFR[0]=(GPIOA->AFR[0]&~((15U<<8)|(15U<<12)))|(7U<<8)|(7U<<12);
    GPIOA->MODER=(GPIOA->MODER&~((3U<<4)|(3U<<6)))|(2U<<4)|(2U<<6);'''
            main+=f'''static void uart_init(void) {{
    {gpio_clock}
    RCC->APB1ENR|=RCC_APB1ENR_USART2EN; (void)RCC->APB1ENR;
    RCC->APB1RSTR|=RCC_APB1RSTR_USART2RST; RCC->APB1RSTR&=~RCC_APB1RSTR_USART2RST;
    {config}
    USART2->BRR={(clock+baud//2)//baud}U;
    USART2->CR1=USART_CR1_UE|USART_CR1_TE|USART_CR1_RE;
}}
static int uart_putc(uint8_t byte) {{
    uint32_t start=ticks_ms;
    while (!(USART2->SR&USART_SR_TXE)) {{
        if ((uint32_t)(ticks_ms-start)>=20U) {{ kiforge_fault|=2U; return 0; }}
    }}
    USART2->DR=byte; return 1;
}}
int main(void) {{
    start_tick();uart_init();delay_ms(1U);
    for (;;) {{
        uint32_t status=USART2->SR;
        if (status&(USART_SR_ORE|USART_SR_FE|USART_SR_NE|USART_SR_PE)) {{
            (void)USART2->DR;kiforge_uart_rx_errors++;continue;
        }}
        if (status&USART_SR_RXNE) {{
            uint8_t byte=(uint8_t)USART2->DR;
            if (!uart_putc(byte)) halt(2U);
        }}
    }}
}}
'''
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
'''
    vectors=['[0]=(void (*)(void))(&_estack)','[1]=Reset_Handler']
    vectors += [f'[{i}]='+('SysTick_Handler' if i==15 else 'Default_Handler') for i in range(2,p['vectorWords']) if i not in p['reservedVectors']]
    startup+=f'__attribute__((section(".isr_vector"),used))\nvoid (*const vectors[{p["vectorWords"]}])(void)={{'+','.join(vectors)+'};\n'
    linker=f'''ENTRY(Reset_Handler)
MEMORY {{ FLASH (rx) : ORIGIN = 0x08000000, LENGTH = {p['flashSize']}
 RAM (rwx) : ORIGIN = 0x20000000, LENGTH = {p['ramSize']} }}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS {{
 .isr_vector : {{ KEEP(*(.isr_vector)) }} > FLASH
 .text : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} > FLASH
 .ARM.exidx : {{ *(.ARM.exidx*) . = ALIGN(4); }} > FLASH
 _sidata = LOADADDR(.data);
 .data : {{ . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; }} > RAM AT > FLASH
 .bss (NOLOAD) : {{ . = ALIGN(4); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .; }} > RAM
 .stack_reserve (NOLOAD) : {{ . = ALIGN(8); . += 1024; }} > RAM
}}
ASSERT((_sidata & 3) == 0, "Flash data source must be word aligned")
ASSERT(((_sdata | _edata | _sbss | _ebss) & 3) == 0, "RAM initialization must be word aligned")
ASSERT(_ebss <= _estack - 1024, "At least 1KB stack headroom is required")
ASSERT(SIZEOF(.isr_vector) == {p['vectorWords']*4}, "Exact target vector table required")
'''
    return {'main.c':main,'startup.c':startup,'linker.ld':linker,'README.md':f'''# {name} / {task}
Normal reset into internal Flash, factory-trimmed HSI {clock//1000000}MHz, no bootloader handover.
No PLL, external crystal, HAL, libc, FPU instructions, USB or RTOS configuration is implied.
LED: PA0 active high. USART2: PA2 TX / PA3 RX. Physical pads are validated against the exact symbol.
Other interrupts use default handlers; application code must supply its own interrupt setup.
F407 CCM is not allocated by this linker. UART timing includes nominal integer-divider review only.
Build and byte checks do not verify hardware behavior, clock accuracy, electrical limits or waveforms.
'''}
