#
# Helper for downloading and setting up prebuilt flash-attention targets.
#
# Manual builds can be used by specifying the install path via DORADO_FLASHATTENTION_PATH.
#

option(DORADO_DISABLE_FLASHATTENTION "Disable optional prebuilt FlashAttention" OFF)
if(DORADO_DISABLE_FLASHATTENTION)
    add_library(dorado_flashattention3 INTERFACE)
    target_compile_definitions(dorado_flashattention3 INTERFACE DORADO_HAS_FLASHATTENTION3=0)
    set(DORADO_HAS_FLASHATTENTION3 FALSE)
    return()
endif()

set(FLASHATTENTION_VERSION "fa4-v4.0.0.beta10")



# Work out what we need to download.
unset(FLASHATTENTION_PATCH_SUFFIX)
unset(FLASHATTENTION_HASH)
if (LINUX)
    if (CMAKE_SYSTEM_PROCESSOR MATCHES "^aarch64*|^arm*")
        if (CUDAToolkit_VERSION VERSION_GREATER_EQUAL 13.0)
            set(FLASHATTENTION_PATCH_SUFFIX "1-cu13.0-Linux-aarch64")
            set(FLASHATTENTION_HASH "881e4a0e499d8d5fa7d8cf043f1e9cfa26d2cf62652f9eed547b2e862d4bb40a")
        endif()
    elseif (CUDAToolkit_VERSION VERSION_GREATER_EQUAL 13.0)
        set(FLASHATTENTION_PATCH_SUFFIX "1-cu13.0-Linux-x86_64")
        set(FLASHATTENTION_HASH "8be268c386855e342e8881a1ec973daeb5c6e65bb71cc9bc0b814d22dc6485d4")
    elseif (CUDAToolkit_VERSION VERSION_GREATER_EQUAL 12.8)
        set(FLASHATTENTION_PATCH_SUFFIX "1-cu12.8-Linux-x86_64")
        set(FLASHATTENTION_HASH "92f36783e1cf6f968ea7d1201237566844d4a1da650f697e24b2aab4813c95e6")
    endif()
elseif (WIN32)
    if (CUDAToolkit_VERSION VERSION_GREATER_EQUAL 13.0)
        set(FLASHATTENTION_PATCH_SUFFIX "1-cu13.0-Windows-x86_64")
        set(FLASHATTENTION_HASH "f3d8662ee08c7385ba409e1e4b29082570eb4a269bc04b06b9e9667e43899305")
    else()
        set(FLASHATTENTION_PATCH_SUFFIX "1-cu12.8-Windows-x86_64")
        set(FLASHATTENTION_HASH "8f0d38be3f91379694c62e622bb0ea6812967f8a4cf9b504607b4c46ff3c791f")
    endif()
endif()



# Check for manual build, otherwise we'll download it.
unset(FLASHATTENTION_PATH)
if (DEFINED DORADO_FLASHATTENTION_PATH)
    message(STATUS "Using local flashattention at: ${DORADO_FLASHATTENTION_PATH}")
    set(FLASHATTENTION_PATH "${DORADO_FLASHATTENTION_PATH}")

elseif (DEFINED FLASHATTENTION_PATCH_SUFFIX)
    # Download the library.
    set(filename "flashattention-${FLASHATTENTION_VERSION}-${FLASHATTENTION_PATCH_SUFFIX}")
    set(url "${DORADO_CDN_URL}/${filename}.zip")
    download_and_extract("${url}" "${filename}" ${FLASHATTENTION_HASH})
    set(FLASHATTENTION_PATH "${DORADO_3RD_PARTY_DOWNLOAD}/${filename}")

endif()



unset(DORADO_HAS_FLASHATTENTION3)
if (DEFINED FLASHATTENTION_PATH)
    # Check that the torch builds match.
    file(READ "${FLASHATTENTION_PATH}/share/torch-hash" FLASHATTENTION_TORCH_HASH)
    if (NOT TORCH_HASH STREQUAL FLASHATTENTION_TORCH_HASH)
        message(FATAL_ERROR "Mismatch between torch builds: flashattention expects ${FLASHATTENTION_TORCH_HASH} but we're using ${TORCH_HASH}")
    endif()

    # Handle platform differences.
    if (WIN32)
        set(lib_prefix "")
        set(lib_suffix "lib")
    else()
        set(lib_prefix "lib")
        set(lib_suffix "a")
    endif()

    message(STATUS "Using flashattention: ${FLASHATTENTION_PATH}")
    set(DORADO_HAS_FLASHATTENTION3 TRUE)

    # Make the target.
    add_library(dorado_flashattention3 STATIC IMPORTED)
    set_target_properties(dorado_flashattention3
        PROPERTIES
            IMPORTED_LOCATION ${FLASHATTENTION_PATH}/lib/${lib_prefix}flashattention3.${lib_suffix}
            INTERFACE_INCLUDE_DIRECTORIES ${FLASHATTENTION_PATH}/include
    )
    target_link_libraries(dorado_flashattention3
        INTERFACE
            torch_lib
    )
    if (NOT WIN32)
        target_link_libraries(dorado_flashattention3
            INTERFACE
                CUDA::cudart
        )
    endif()

else()
    message(STATUS "No flashattention support")
    set(DORADO_HAS_FLASHATTENTION3 FALSE)

    # Dummy target if we don't support it on this build.
    add_library(dorado_flashattention3 INTERFACE)

endif()

target_compile_definitions(dorado_flashattention3
    INTERFACE
        DORADO_HAS_FLASHATTENTION3=$<BOOL:${DORADO_HAS_FLASHATTENTION3}>
)
