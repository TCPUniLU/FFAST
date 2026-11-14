use pyo3::prelude::*;

/// The contract number. FFAST accepts only the number it expects.
#[pyfunction]
fn contract() -> u32 {
    1
}

#[pymodule]
fn _ffast_xyz(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(contract, m)?)?;
    Ok(())
}
