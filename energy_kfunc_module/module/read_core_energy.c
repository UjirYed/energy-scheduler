#include <linux/init.h>       // Macros for module initialization
#include <linux/module.h>     // Core header for loading modules
#include <linux/kernel.h>     // Kernel logging macros
#include <linux/bpf.h>
#include <linux/btf.h>
#include <linux/btf_ids.h>
#include <linux/version.h>

/* Compat for kernels < 6.9/6.16 (e.g. Ubuntu 24.04's 6.8). */
#ifndef BTF_KFUNCS_START
#define BTF_KFUNCS_START(name) BTF_SET8_START(name)
#define BTF_KFUNCS_END(name) BTF_SET8_END(name)
#endif
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 16, 0)
#define rdmsrq_safe rdmsrl_safe
#endif

// CPU CORE
#define ENERGY_PWR_UNIT_MSR	0xC0010299
#define ENERGY_CORE_MSR		0xC001029A
#define ENERGY_PKG_MSR		0xC001029B
#define AMD_ENERGY_UNIT_MASK	0x01F00
#define AMD_ENERGY_MASK		0xFFFFFFFF

// DRAM
#define DRAM_ENERGY_MSR 0x00000619

// Integrated GPU
#define INTEGRATED_GPU_MSR 0x00000641

// PACKAGE ENERGY
#define PACKAGE_ENERGY_MSR 0xC001029B


int energy_units;


struct energy_measurement {
    u64 core_energy;
    u64 dram_energy;
    u64 gpu_energy;
    u64 package_energy;
};

/* Declare the kfunc prototype */
__bpf_kfunc void read_core_energy(struct energy_measurement* result);

static void get_energy_units(void);

/* Begin kfunc definitions */
__bpf_kfunc_start_defs();


/* Define the read_core_energy kfunc */
__visible noinline __bpf_kfunc void read_core_energy(struct energy_measurement* result)
{   
    u64 core;
    u64 dram;
    u64 gpu;
    u64 package;
    rdmsrq_safe(ENERGY_CORE_MSR, &core);
    rdmsrq_safe(DRAM_ENERGY_MSR, &dram);
    rdmsrq_safe(INTEGRATED_GPU_MSR, &gpu);
    rdmsrq_safe(ENERGY_PKG_MSR, &package);

    pr_info("KFUNC: before package=%llu\n", package);

    result->core_energy = div64_ul(core * 1000000UL, BIT(energy_units));
    result->dram_energy = div64_ul(dram * 1000000UL, BIT(energy_units));    
    result->gpu_energy = div64_ul(gpu * 1000000UL, BIT(energy_units));        
    result->package_energy = div64_ul(package * 1000000UL, BIT(energy_units));    
    pr_info("KFUNC: after package=%llu\n", result->package_energy);
}

/* End kfunc definitions */
__bpf_kfunc_end_defs();

/* Define the BTF kfuncs ID set */
BTF_KFUNCS_START(bpf_kfunc_example_ids_set)
BTF_ID_FLAGS(func, read_core_energy)
BTF_KFUNCS_END(bpf_kfunc_example_ids_set)

/* Register the kfunc ID set */
static const struct btf_kfunc_id_set bpf_kfunc_example_set = {
    .owner = THIS_MODULE,
    .set = &bpf_kfunc_example_ids_set,
};

static void get_energy_units()
{
    u64 rapl_units;

    // #if LINUX_VERSION_CODE < KERNEL_VERSION(6, 16, 0)
    //     rdmsrl_safe(ENERGY_PWR_UNIT_MSR, &rapl_units);
    // #else
    rdmsrq_safe(ENERGY_PWR_UNIT_MSR, &rapl_units);
    //#endif
    energy_units = (rapl_units & AMD_ENERGY_UNIT_MASK) >> 8;
}

/* Function executed when the module is loaded */
static int __init read_core_energy_init(void)
{
    int ret;

    printk(KERN_INFO "Hello, world!\n");
    /* Register the BTF kfunc ID set for BPF_PROG_TYPE_STRUCT_OPS */
    ret = register_btf_kfunc_id_set(BPF_PROG_TYPE_STRUCT_OPS, &bpf_kfunc_example_set);
    // ret = register_btf_kfunc_id_set(BPF_, &bpf_kfunc_example_set);
    if (ret)
    {
        pr_err("bpf_kfunc_example: Failed to register BTF kfunc ID set\n");
        return ret;
    }
    get_energy_units();
    printk(KERN_INFO "bpf_kfunc_example: Module loaded successfully\n");
    return 0; // Return 0 if successful
}

/* Function executed when the module is removed */
static void __exit read_core_energy_exit(void)
{
 // Do nothing
}

/* Macros to define the module’s init and exit points */
module_init(read_core_energy_init);
module_exit(read_core_energy_exit);

MODULE_LICENSE("GPL");                 // License type (GPL)
MODULE_AUTHOR("Your Name");            // Module author
MODULE_DESCRIPTION("Kernel module with kfunc for RAPL readings"); // Module description
MODULE_VERSION("1.0");                 // Module version
